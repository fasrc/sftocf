import os
import re
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

import requests
import pandas as pd
from django.utils import timezone
from django.contrib.auth import get_user_model

from coldfront.core.utils.common import import_from_settings
from coldfront.core.project.models import Project
from coldfront.core.allocation.models import Allocation, AllocationUserStatusChoice

MISSING_DATA_DIR = './local_data/missing/'


datestr = datetime.today().strftime("%Y%m%d")
logger = logging.getLogger(__name__)
logger.propagate = False
logger.setLevel(logging.DEBUG)
filehandler = logging.FileHandler(f'coldfront/plugins/sftocf/data/logs/sfc{datestr}.log', 'w')
logger.addHandler(filehandler)


with open("coldfront/plugins/sftocf/servers.json", "r") as myfile:
    svp = json.loads(myfile.read())

def record_process(func):
    """Wrapper function for logging"""
    def call(*args, **kwargs):
        funcdata = "{} {}".format(func.__name__, func.__code__.co_firstlineno)
        logger.debug("\n%s START.", funcdata)
        result = func(*args, **kwargs)
        logger.debug("%s END. output:\n%s\n", funcdata, result)
        return result
    return call

class StarFishServer:
    """Class for interacting with StarFish API.
    """

    def __init__(self, server, url):
        self.name = server
        self.api_url = f"{url}/api/"
        self.token = self.get_auth_token()
        self.headers = generate_headers(self.token)
        self.volumes = self.get_volume_names()

    @record_process
    def get_auth_token(self):
        """Obtain a token through the auth endpoint.
        """
        username = import_from_settings('SFUSER')
        password = import_from_settings('SFPASS')
        auth_url = self.api_url + "auth/"
        todo = {"username": username, "password": password}
        response = requests.post(auth_url, json=todo)
        # response.status_code
        response_json = response.json()
        token = response_json["token"]
        return token


    # 2A. Generate list of volumes to search, along with top-level paths
    @record_process
    def get_volume_names(self):
        """ Generate a list of the volumes available on the server.
        """
        stor_url = self.api_url + "storage/"
        response = return_get_json(stor_url, self.headers)
        volnames = [i["name"] for i in response["items"]]
        return volnames

    @record_process
    def get_subpaths(self, volpath):
        """Generate list of directories in top layer of designated volpath.
        Parameters
        ----------
        volpath : string
            The volume and path.
        Returns
        -------
        subpaths : list of strings
        """
        getsubpaths_url = self.api_url + "storage/" + volpath
        request = return_get_json(getsubpaths_url, self.headers)
        pathdicts = request["items"]
        subpaths = [i["Basename"] for i in pathdicts]
        return subpaths

    def create_query(self, query, group_by, volpath, sec=3):
        """Produce a Query class object.
        Parameters
        ----------
        query : string
        group_by : string
        volpath : string
        sec : integer, optional

        Returns
        -------
        query : Query class object
        """
        query = StarFishQuery(
            self.headers, self.api_url, query, group_by, volpath, sec=sec
        )
        return query

    @record_process
    def get_vol_membership(self, volume, mtype):
        """Get the membership of the provided volume.
        """
        url = self.api_url + f"mapping/{mtype}_membership?volume_name=" + volume
        member_list = return_get_json(url, self.headers)
        return member_list



class StarFishQuery:
    """

    Attributes
    ----------
    api_url : str
    headers : dict
    query_id : str
    result : list

    Methods
    -------
    post_async_query(query, group_by, volpath)
    return_results_once_prepared(sec=3)
    return_query_result()
    """

    def __init__(self, headers, api_url, query, group_by, volpath, sec=3):
        self.api_url = api_url
        self.headers = headers
        self.query_id = self.post_async_query(query, group_by, volpath)
        self.result = self.return_results_once_prepared(sec=sec)

    @record_process
    def post_async_query(self, query, group_by, volpath):
        """Post an asynchronous query through the Starfish API.
        """
        query_url = self.api_url + "async/query/"
        params = {
            "volumes_and_paths": volpath,
            "queries": query,
            "format": "parent_path fn type size blck ct mt at uid gid mode",
            "sort_by": group_by,
            "group_by": group_by,
            "limit": "100000",
            "force_tag_inherit": "false",
            "output_format": "json",
            "delimiter": ",",
            "escape_paths": "false",
            "print_headers": "true",
            "size_unit": "B",
            "humanize_nested": "false",
            "mount_agent": "None",
        }
        req = requests.post(query_url, params=params, headers=self.headers)
        response = req.json()
        logger.debug("response: %s", response)
        return response["query_id"]

    @record_process
    def return_results_once_prepared(self, sec=3):
        """Wait for posted query to return result.
        """
        while True:
            query_check_url = self.api_url + "async/query/" + self.query_id
            response = return_get_json(query_check_url, self.headers)
            if response["is_done"] == True:
                result = self.return_query_result()
                return result
            time.sleep(sec)

    def return_query_result(self):
        """Go to link for query result and return the JSON.
        """
        query_result_url = self.api_url + "async/query_result/" + self.query_id
        response = return_get_json(query_result_url, self.headers)
        return response


class ColdFrontDB:
    """

    Attributes
    ----------

    Methods
    -------
    produce_lab_dict(self, vol)
    check_volume_collection(self, lr, homepath="./coldfront/plugins/sftocf/data/")
    pull_sf(self, volume=None)
    push_cf(self, filepaths, clean)
    update_user_usage(self, user, userdict, allocation)
    """

    @record_process
    def produce_lab_dict(self, vol):
        """Create dict of labs to collect and the volumes/tiers associated with them.
        Parameters
        ----------
        vol : string
            If not None, collect only allocations on the specified volume
        Returns
        -------
        labs_resources: dict
            Structured as follows:
            "lab_name": [("volume", "tier"),("volume", "tier")]
        """
        pr_objs = Allocation.objects.only("id", "project")
        pr_dict = {allocation.project.title: [] for allocation in pr_objs}
        for allocation in pr_objs:
            proj_name = allocation.project.title
            resource_list = allocation.get_resources_as_string.split(", ")
            pr_dict[proj_name].extend(resource_list)
        labs_resources = pr_dict if not vol else {
            p:[i for i in r if vol in i] for p, r in pr_dict.items()
        }
        logger.debug("labs_resources:\n%s", labs_resources)
        return labs_resources


    def check_volume_collection(self, lr, homepath="./coldfront/plugins/sftocf/data/"):
        """
        for each lab-resource combination in parameter lr, check existence of corresponding
        file in data path. If a file for that lab-resource combination that is <2 days old
        exists, mark it as collected. If not, slate lab-resource combination for collection.

        Parameters
        ----------
        lr : dict
            Keys are labnames, values are a list of (volume, tier) tuples.

        Returns
        -------
        filepaths : list
            List of lab usage files that have already been created.
        to_collect : list
            list of tuples - (labname, volume, tier, filename)
        """
        filepaths = []
        to_collect = []
        labs_resources = [(l, res) for l, r in lr.items() for res in r]
        logger.debug("labs_resources:%s", labs_resources)

        yesterdaystr = (datetime.today()-timedelta(1)).strftime("%Y%m%d")
        dates = [yesterdaystr, datestr]

        for lr_pair in labs_resources:
            lab = lr_pair[0]
            resource = lr_pair[1]
            fpaths = [f"{homepath}{lab}_{resource}_{n}.json" for n in dates]
            if any(Path(fpath).exists() for fpath in fpaths):
                for fpath in fpaths:
                    if Path(fpath).exists():
                        filepaths.append(fpath)
            else:
                to_collect.append((lab, resource, fpaths[-1],))

        return filepaths, to_collect


    def pull_sf(self, volume=None):
        """Query Starfish to produce json files of lab usage data.
        Return a set of produced filepaths.
        """
        # 1. produce dict of all labs to be collected & volumes on which their data is located
        lab_res = self.produce_lab_dict(volume)
        # 2. produce list of files collected & list of lab/volume/filename tuples to collect
        filepaths, to_collect = self.check_volume_collection(lab_res)
        # 3. produce set of all volumes to be queried
        vol_set = {i[1] for i in to_collect}
        servers_vols = [
            (k, vol) for k, v in svp.items() for vol in vol_set if vol in v['volumes']
        ]
        for server_vol in servers_vols:
            srv = server_vol[0]
            vol = server_vol[1]
            paths = svp[srv]["volumes"][vol]
            to_collect_subset = [t for t in to_collect if t[1] == vol]
            logger.debug("vol: %s\nto_collect_subset: %s", vol, to_collect_subset)
            server = StarFishServer(srv, svp[srv]['url'])
            fpaths = collect_starfish_usage(server, vol, paths, to_collect_subset)
            filepaths.extend(fpaths)
        return set(filepaths)


    def push_cf(self, filepaths, clean):
        """
        Iterate through JSON files in the filepaths list and insert the data
        into the database.
        """
        for file in filepaths:
            content = read_json(file)
            usernames = [d['username'] for d in content['contents']]
            resource = content['volume']

            user_models, missing_usernames = id_present_missing_users(usernames)
            print("missing_usernames", missing_usernames)
            log_missing('user', missing_usernames)

            project = Project.objects.get(title=content['project'])
            # find project allocation
            allocations = Allocation.objects.filter(
                project=project, resources__name=resource
            )
            if Allocation.MultipleObjectsReturned:
                logger.warning(' '.join(["WARNING: Multiple allocations found"
                        "for project id %s, resource %s. Updating the first."]),
                        project.id, resource)
            allocation = allocations.first()
            logger.debug(
                "%s\nusernames: %s\nuser_models: %s",
                project.title, usernames, [u.username for u in user_models]
            )

            for user in user_models:
                userdict = [d for d in content['contents'] if d['username'] == user.username][0]
                model = user_models.get(username=userdict['username'])
                self.update_user_usage(model, userdict, allocation)
            if clean:
                os.remove(file)
        logger.debug("push_cf complete")


    def update_user_usage(self, user, userdict, allocation):
        """Update usage, unit, and usage_bytes values for the designated user.
        """
        usage, unit = split_num_string(userdict["size_sum_hum"])
        logger.debug("entering for user: %s", user.username)

        allocationuser, created = allocation.allocationuser_set.get_or_create(
            user=user,
            defaults={
                'created': timezone.now(),
                'status': AllocationUserStatusChoice.objects.get(name='Active')
            }
        )
        if created:
            logger.info('allocation user %s created', allocationuser)
        allocationuser.usage_bytes = userdict['size_sum']
        allocationuser.usage = usage
        allocationuser.unit = unit
        # automatically updates "modified" field & adds old record to history
        allocationuser.save()
        logger.debug("successful entry: %s, %s", userdict['groupname'], userdict['username'])



def clean_data_dir(homepath):
    """Remove json from data folder that's more than a week old
    """
    files = os.listdir(homepath)
    json_files = [f for f in files if ".json" in f]
    fpaths = [f"{homepath}{f}" for f in json_files]
    now = time.time()
    for fpath in fpaths:
        created = os.stat(fpath).st_ctime
        if created < now - 7 * 86400:
            os.remove(fpath)

def write_update_file_line(filepath, patterns):
    with open(filepath, 'a+') as file:
        file.seek(0)
        for pattern in patterns:
            if not any(pattern == line.rstrip('\r\n') for line in file):
                file.write(pattern + '\n')

def split_num_string(string):
    num = re.search(r"\d*\.?\d+", string).group()
    size = string.replace(num, "")
    return num, size

def return_get_json(url, headers):
    """return JSON from the designated url using the designated headers.
    """
    response = requests.get(url, headers=headers)
    return response.json()

def save_json(file, contents):
    """save contents to designated file as JSON.
    """
    with open(file, "w") as writefile:
        json.dump(contents, writefile, sort_keys=True, indent=4)

def read_json(filepath):
    """from the designated JSON filepath, return the contents.
    """
    logger.debug("read_json for %s", filepath)
    with open(filepath, "r") as json_file:
        data = json.loads(json_file.read())
    return data

def locate_or_create_dirpath(dpath):
    """Check whether a directory exists. If it doesn't, create the directory.
    """
    if not os.path.exists(dpath):
        os.makedirs(dpath)
        logger.info("created new directory %s", dpath)

@record_process
def collect_starfish_usage(server, volume, volumepath, projects):
    """
    Parameters
    ----------
    server : object
    volume : string
    volumepath : list of strings
    projects : list of tuples

    Returns
    -------
    filepaths : list of strings
    """
    filepaths = []
    datestr = datetime.today().strftime("%Y%m%d")
    locate_or_create_dirpath("./coldfront/plugins/sftocf/data/")
    logger.debug("projects: %s", projects)
    for project in projects:
        p = project[0]
        tier = project[1]
        filepath = project[2]
        lab_volpath = volumepath[1] if "_l3" in p else volumepath[0]
        logger.debug("filepath: %s lab: %s volpath: %s", filepath, p, lab_volpath)
        usage_query = server.create_query(
            f"type=f groupname={p}", "username, groupname", f"{volume}:{lab_volpath}"
        )
        data = usage_query.result
        logger.debug("usage_query.result: %s", data)
        if not data:
            logger.warning("No starfish result for lab %s", p)
        elif isinstance(data, dict) and "error" in data:
            logger.warning("Error in starfish result for lab %s:\n%s", p, data)
        else:
            data = usage_query.result
            logger.debug(data)
            record = {
                "server": server.name,
                "volume": volume,
                "path": lab_volpath,
                "project": p,
                "tier": tier,
                "date": datestr,
                "contents": data,
            }
            save_json(filepath, record)
            filepaths.append(filepath)
    return filepaths



def id_present_missing_users(username_list):
    """
    Collect all User entries with usernames in param username_list; return tuple
    of all matching User entries and all usernames with no User entries.
    """
    present_users = get_user_model().objects.filter(username__in=username_list)
    present_usernames = list(present_users.values_list('username', flat=True))
    missing_usernames = [
        {'username': name} for name in username_list if name not in present_usernames
    ]
    return (present_users, missing_usernames)


def update_csv(new_entries, dirpath, csv_name, date_update='date'):
    """Add or update entries in CSV, order CSV by descending date and save.

    Parameters
    ----------
    new_entries : list of dicts
        identifying information to record for missing entries:
        for users, "username".
        for projects, "title".
        for allocations, "resource_name", "project_title", and "path".
    dirpath : str
    csv_name : str
    date_update : str
    """
    if new_entries:
        locate_or_create_dirpath(dirpath)
        fpath = f'{dirpath}{csv_name}'
        try:
            df = pd.read_csv(fpath, parse_dates=[date_update])
        except FileNotFoundError:
            df = pd.DataFrame()
        except ValueError:
            df = pd.read_csv(fpath)
        new_records = pd.DataFrame(new_entries)
        col_checks = new_records.columns.values.tolist()
        new_records[date_update] = datetime.today()
        updated_df = (pd.concat([df, new_records])
                        .drop_duplicates(col_checks, keep='last')
                        .sort_values(date_update, ascending=False)
                        .reset_index(drop=True))
        updated_df.to_csv(fpath, index=False)


def log_missing(modelname, missing):
    """log missing entries for a given Coldfront model.
    Add or update entries in CSV, order CSV by descending date and save.

    Parameters
    ----------
    modelname : string
        lowercase name of the Coldfront model for "missing"
    missing : list of dicts
        identifying information to record for missing entries:
            for users, "username".
            for projects, "title".
            for allocations, "resource_name", "project_title", and "path".
    """
    update_csv(missing, MISSING_DATA_DIR, f'missing_{modelname}s.csv')


def generate_headers(token):
    """Generate "headers" attribute by using the "token" attribute.
    """
    headers = {
        "accept": "application/json",
        "Authorization": "Bearer {}".format(token),
    }
    return headers
