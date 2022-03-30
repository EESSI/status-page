#!/usr/bin/env python3

import datetime
import time

from jinja2 import Environment, FileSystemLoader

from cvmfsscraper.main import scrape, scrape_server

env= Environment(loader=FileSystemLoader("templates/"))
template = env.get_template("status.html.j2")

NOW = time.time()
ACCEPTED_TIME_SINCE_SNAPSHOT_IN_SECONDS = 60 * 60 * 3 # Three hours.

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

def get_class(status):
    return LEGEND[status]["class"]

def get_text(status):
    return LEGEND[status]["text"]

def get_desc(status):
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
    stratum0 = [
        "rug-nl.stratum0.cvmfs.eessi-infra.org",
    ],    
    stratum1 = [
        "aws-eu-west1.stratum1.cvmfs.eessi-infra.org",
        "azure-us-east1.stratum1.cvmfs.eessi-infra.org",
        "bgo-no.stratum1.cvmfs.eessi-infra.org",
        "rug-nl.stratum1.cvmfs.eessi-infra.org",
        "broken.stratum1.cvmfs.eessi-infra.org",
    ],
    ignore_repos = [
        "bla.eessi-hpc.org",
        "bob.eessi-hpc.org",
        "ci.eessi-hpc.org",
    ],
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

            if NOW - repo.last_snapshot > ACCEPTED_TIME_SINCE_SNAPSHOT_IN_SECONDS:
                rs = repo_snap_status[repo.last_snapshot]
                if rs == ok_class():
                    repo_snap_status[repo.name] = degraded_class()

        shortname = server.name.split('.')

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
    shortname = repo.split('.')
    repositories.append(
        {
            "name": shortname[0],
            "revision_class": repo_rev_status[repo],
            "snapshot_class": repo_snap_status[repo],
        }
    )

for repo in stratum0_repo_versions:
    shortname = repo.split('.')
    stratum0_details.append(shortname[0] + " : " + str(stratum0_repo_versions[repo]))


if len(stratum1_not_ok_events) >= stratum1_count:
    # More errors than nodes (errors are one per node)
    stratum1_status_class = failed_class()
elif len(stratum1_not_ok_events) >= stratum1_count - 2:
    # We have at most two error free nodes
    stratum1_status_class = warning_class()
elif stratum1_not_ok_events:
    stratum1_status_class = degraded_class()



data = {
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

    "last_update": datetime.datetime.now().replace(microsecond=0).isoformat(),
}

output = template.render(data)

with open("status-generated.html", "w") as fh:
    fh.write(output)

