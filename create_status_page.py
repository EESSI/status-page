#!/usr/bin/env python3

import configparser
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Union

from cvmfsscraper import scrape, set_log_level
from jinja2 import Environment, FileSystemLoader


def read_config(file_path: str) -> Dict[str, List[str]]:
    """
    Reads configuration from an ini file, sets a valid logging level,
    and raises an exception if an invalid logging level is provided.

    :param file_path: Path to the configuration file.
    :returns: Dictionary with configuration parameters and logging level.
    :raises ValueError: If an invalid logging level is provided.
    """
    config = configparser.ConfigParser(allow_no_value=True)
    config.read(file_path)

    CONFIG_SECTIONS_THAT_MUST_HAVE_VALUES = ["stratum0", "stratum1"]

    def get_section_keys(section: str) -> List[str]:
        if section not in config:
            raise ValueError(f"Section {section} not found in configuration file")

        return [key for key in config[section]]

    # Retrieve and validate logging level
    logging_level_str = config.get("config", "logging_level", fallback="INFO").upper()
    if logging_level_str not in logging._nameToLevel:
        raise ValueError(f"Invalid logging level: {logging_level_str}")

    logging_level = logging.getLevelName(logging_level_str)

    conf: Dict[str, Union[List[str], int]] = {
        "stratum0_servers": get_section_keys("stratum0"),
        "stratum1_servers": get_section_keys("stratum1"),
        "repos": get_section_keys("repositories"),
        "ignore_repos": get_section_keys("ignored_repositories"),
        "logging_level": logging_level,
        "min_ok_stratum1": config.getint("config", "min_ok_stratum1", fallback=2),
        "contact_email": config.get("config", "contact_email", fallback=""),
        "title": config.get("config", "title", fallback="EESSI :: Status"),
    }

    for section in CONFIG_SECTIONS_THAT_MUST_HAVE_VALUES:
        if not config[section]:
            raise ValueError(f"Section {section} must have at least one value")

    return conf


config_path = "config.ini"
try:
    config = read_config(config_path)
except ValueError as e:
    print(f"Error in configuration: {e}")
    sys.exit(1)

set_log_level(config["logging_level"])

env = Environment(loader=FileSystemLoader("templates/"))
template = env.get_template("status.html.j2")

NOW = datetime.now()
ACCEPTED_TIME_SINCE_SNAPSHOT_IN_SECONDS = 60 * 60 * 3  # Three hours.

LEGEND = {
    "OK": {
        "class": "status-ok fas fa-check",
        "text": "Normal service",
        "description": "EESSI services operating without issues.",
    },
    "DEGRADED": {
        "class": "status-degraded fas fa-minus-square",
        "text": "Degraded",
        "description": "EESSI services are operational and may be used as expected, but performance may be affected.",
    },
    "WARNING": {
        "class": "status-warning fas fa-exclamation-triangle",
        "text": "Warning",
        "description": "EESSI services are operational, but some systems may be unavailable or out of sync.",
    },
    "FAILED": {
        "class": "status-failed fas fa-times-circle",
        "text": "Failed",
        "description": "EESSI services have failed.",
    },
    "MAINTENANCE": {
        "class": "status-maintenance fas fa-hammer",
        "text": "Maintenance",
        "description": "EESSI services are unavailable due to scheduled maintenance.",
    },
}


def get_class(status: str):
    return LEGEND[status]["class"]


def get_text(status: str):
    return LEGEND[status]["text"]


def get_desc(status: str):
    return LEGEND[status]["description"]


def ok_class():
    return get_class("OK")


def degraded_class():
    return get_class("DEGRADED")


def warning_class():
    return get_class("WARNING")


def failed_class():
    return get_class("FAILED")


def maintenance_class():
    return get_class("MAINTENANCE")


servers = scrape(
    stratum0_servers=config["stratum0_servers"],
    stratum1_servers=config["stratum1_servers"],
    repos=config["repos"],
    ignore_repos=config["ignore_repos"],
)

# Contains events that are bad. 1 is degraded, 2 warning, 3 failure
eessi_not_ok_events = []
stratum1_not_ok_events = []
repositories_not_ok_events = []

stratum1_count = 0

repo_rev_status = {}
repo_snap_status = {}

known_repos = {}

eessi_status = "OK"
eessi_status_class = get_class(eessi_status)
eessi_status_text = get_text(eessi_status)
eessi_status_description = get_desc(eessi_status)

stratum0_repo_versions = {}
stratum0_status_class = ok_class()
stratum0_details = []

stratum1_status_class = ok_class()
stratum1_servers = [
    #    {
    #        "name": "bgo-no",
    #        "update_class": get_class("OK"),
    #        "geoapi_class": get_class("DEGRADED"),
    #    },
    #
    #    {
    #        "name": "rug-nl",
    #        "update_class": get_class("DEGRADED"),
    #        "geoapi_class": get_class("FAILED"),
    #    },
]

repositories_status_class = ok_class()
repositories = [
    #    {
    #        "name": "pilot",
    #        "revision_class": get_class("OK"),
    #        "snapshot_class": get_class("OK"),
    #    },
    #
    #    {
    #        "name": "ci",
    #        "revision_class": get_class("FAILED"),
    #        "snapshot_class": get_class("FAILED"),
    #    },
]


# First get reference data from stratum0
for server in servers:
    if server.server_type == 0:
        for repo in server.repositories:
            stratum0_repo_versions[repo.name] = repo.revision

for server in servers:
    if server.server_type == 1:
        stratum1_count = stratum1_count + 1
        default_class = ok_class()
        if server.is_down():
            default_class = failed_class()
            stratum1_not_ok_events.append(3)

        updates = default_class

        for repo in stratum0_repo_versions:
            repo_rev_status[repo] = failed_class()
            repo_snap_status[repo] = failed_class()
            known_repos[repo] = 1

        for repo in server.repositories:
            # Pure initialization, we'll find problems later.
            repo_rev_status[repo.name] = default_class
            repo_snap_status[repo.name] = default_class
            known_repos[repo.name] = 1

            if repo.revision != stratum0_repo_versions[repo.name]:
                updates = warning_class()
                eessi_not_ok_events.append(2)
                stratum1_not_ok_events.append(2)
                repositories_not_ok_events.append(2)

                rs = repo_rev_status[repo.name]
                # Escalate, this is ugly, should keep track of all the issues and pick the worst.
                if rs == ok_class() or rs == degraded_class() or rs == failed_class():
                    repo_rev_status[repo.name] = warning_class()

            if repo.last_snapshot:
                # Calculate the time difference as a timedelta and get the total seconds
                time_difference = NOW - repo.last_snapshot
                if (
                    time_difference.total_seconds()
                    > ACCEPTED_TIME_SINCE_SNAPSHOT_IN_SECONDS
                ):
                    rs = repo_snap_status[repo.last_snapshot]
                    if rs == ok_class():
                        repo_snap_status[repo.name] = degraded_class()
            else:
                repo_snap_status[repo.name] = failed_class()
                repositories_not_ok_events.append(2)

        shortname = server.name.split(".")

        geoapi_class = default_class
        # 0 ok, 1 wrong answer, 2 failing, 9 unable to test
        if server.geoapi_status == 0:
            geoapi_class = ok_class()
        elif server.geoapi_status == 1:
            geoapi_class = degraded_class()
        elif server.geoapi_status == 2:
            geoapi_class = failed_class()
        else:
            server.geoapi_status = warning_class()

        stratum1_servers.append(
            {
                "name": shortname[0],
                "update_class": updates,
                "geoapi_class": geoapi_class,
            },
        )

for repo in known_repos:
    shortname = repo.split(".")
    repositories.append(
        {
            "name": shortname[0],
            "revision_class": repo_rev_status[repo],
            "snapshot_class": repo_snap_status[repo],
        }
    )

for repo in stratum0_repo_versions:
    shortname = repo.split(".")
    stratum0_details.append(shortname[0] + " : " + str(stratum0_repo_versions[repo]))


if len(stratum1_not_ok_events) >= stratum1_count:
    # More errors than nodes (errors are one per node)
    stratum1_status_class = failed_class()
elif len(stratum1_not_ok_events) > stratum1_count - config["min_ok_stratum1"]:
    # We have at most two error free nodes
    stratum1_status_class = warning_class()
elif stratum1_not_ok_events:
    stratum1_status_class = degraded_class()


data: Dict[str, Any] = {
    "legend": LEGEND,
    "eessi_status_class": eessi_status_class,
    "eessi_status_text": eessi_status_text,
    "eessi_status_description": eessi_status_description,
    "stratum0_status_class": stratum0_status_class,
    "stratum0_details": stratum0_details,
    "stratum1_status_class": stratum1_status_class,
    "stratum1s": stratum1_servers,
    "repositories_status_class": repositories_status_class,
    "repositories": repositories,
    "last_update": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z"),
    "contact_email": config["contact_email"],
    "title": config["title"],
}

output = template.render(data)

with open("status-generated.html", "w") as fh:
    fh.write(output)
