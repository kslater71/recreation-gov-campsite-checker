# -*- coding: utf-8 -*-
#!/usr/bin/env python3

import json
import logging
import sys
import os
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import count, groupby
import copy

from dateutil import rrule

from clients.recreation_client import RecreationClient
from enums.date_format import DateFormat
from enums.emoji import Emoji
from utils import formatter
from utils.camping_argparser import CampingArgumentParser

LOG = logging.getLogger(__name__)
log_formatter = logging.Formatter(
    "%(asctime)s - %(process)s - %(levelname)s - %(message)s"
)
sh = logging.StreamHandler()
sh.setFormatter(log_formatter)
LOG.addHandler(sh)


def get_park_information(
    park_id, start_date, end_date, campsite_type=None, campsite_ids=()
):
    """
    This function consumes the user intent, collects the necessary information
    from the recreation.gov API, and then presents it in a nice format for the
    rest of the program to work with. If the API changes in the future, this is
    the only function you should need to change.

    The only API to get availability information is the `month?` query param
    on the availability endpoint. You must query with the first of the month.
    This means if `start_date` and `end_date` cross a month boundary, we must
    hit the endpoint multiple times.

    The output of this function looks like this:

    {"<campsite_id>": [<date>, <date>]}

    Where the values are a list of ISO 8601 date strings representing dates
    where the campsite is available.

    Notably, the output doesn't tell you which sites are available. The rest of
    the script doesn't need to know this to determine whether sites are available.
    """

    # Get each first of the month for months in the range we care about.
    start_of_month = datetime(start_date.year, start_date.month, 1)
    months = list(
        rrule.rrule(rrule.MONTHLY, dtstart=start_of_month, until=end_date)
    )

    # Get data for each month.
    api_data = []
    for month_date in months:
        api_data.append(RecreationClient.get_availability(park_id, month_date))

    # Collapse the data into the described output format.
    # Filter by campsite_type if necessary.
    data = {}
    data2 = {} # additional information on the campsite, excluding "availabilities"

    for month_data in api_data:
        for campsite_id, campsite_data in month_data["campsites"].items():
            LOG.debug("campsite_data="+json.dumps(campsite_data))
            available = []
            a = data.setdefault(campsite_id, [])

            # get addition campsite data, excluding "availabilities" and "quantities"
            campsite_info=copy.deepcopy(campsite_data)
            campsite_info.pop('availabilities', None)
            campsite_info.pop('quantities', None)
            campsite_info.pop('campsite_id', None)
            data2.setdefault(campsite_id, campsite_info)

            for date, availability_value in campsite_data["availabilities"].items():
                if availability_value != "Available":
                    continue

                if (
                    campsite_type
                    and campsite_type != campsite_data["campsite_type"]
                ):
                    continue

                if (
                    len(campsite_ids) > 0
                    and int(campsite_data["campsite_id"]) not in campsite_ids
                ):
                    continue

                available.append(date)
            if available:
                a += available
    # print('\ndata=')
    # print(data)
    # print('\ndata2=')
    # print(data2)
    return data, data2


def get_num_available_sites(
    park_information, start_date, end_date, nights=None
):
    maximum = len(park_information)

    num_available = 0
    num_days = (end_date - start_date).days
    dates = [end_date - timedelta(days=i) for i in range(1, num_days + 1)]
    dates = set(
        formatter.format_date(
            i, format_string=DateFormat.ISO_DATE_FORMAT_RESPONSE.value
        )
        for i in dates
    )

    if nights not in range(1, num_days + 1):
        nights = num_days
        LOG.debug("Setting number of nights to {}.".format(nights))

    available_dates_by_campsite_id = defaultdict(list)
    for site, availabilities in park_information.items():
        # List of dates that are in the desired range for this site.
        desired_available = []

        for date in availabilities:
            if date not in dates:
                continue
            desired_available.append(date)

        if not desired_available:
            continue

        appropriate_consecutive_ranges = consecutive_nights(
            desired_available, nights
        )

        if appropriate_consecutive_ranges:
            num_available += 1
            LOG.debug("Available site {}: {}".format(num_available, site))

        for r in appropriate_consecutive_ranges:
            start, end = r
            available_dates_by_campsite_id[int(site)].append(
                {"start": start, "end": end}
            )

    return num_available, maximum, available_dates_by_campsite_id


def consecutive_nights(available, nights):
    """
    Returns a list of dates from which you can start that have
    enough consecutive nights.

    If there is one or more entries in this list, there is at least one
    date range for this site that is available.
    """
    ordinal_dates = [
        datetime.strptime(
            dstr, DateFormat.ISO_DATE_FORMAT_RESPONSE.value
        ).toordinal()
        for dstr in available
    ]
    c = count()

    consecutive_ranges = list(
        list(g) for _, g in groupby(ordinal_dates, lambda x: x - next(c))
    )

    long_enough_consecutive_ranges = []
    for r in consecutive_ranges:
        # Skip ranges that are too short.
        if len(r) < nights:
            continue
        for start_index in range(0, len(r) - nights + 1):
            start_nice = formatter.format_date(
                datetime.fromordinal(r[start_index]),
                format_string=DateFormat.INPUT_DATE_FORMAT.value,
            )
            end_nice = formatter.format_date(
                datetime.fromordinal(r[start_index + nights - 1] + 1),
                format_string=DateFormat.INPUT_DATE_FORMAT.value,
            )
            long_enough_consecutive_ranges.append((start_nice, end_nice))

    return long_enough_consecutive_ranges


def check_park(park_id, start_date, end_date, campsite_type, campsite_ids=(), nights=None):
    park_information, park_additional_information = get_park_information(
        park_id, start_date, end_date, campsite_type, campsite_ids
    )
    LOG.debug(
        "Information for park {}: {}".format(
            park_id, json.dumps(park_information, indent=2)
        )
    )
    park_name = RecreationClient.get_park_name(park_id)
    current, maximum, availabilities_filtered = get_num_available_sites(
        park_information, start_date, end_date, nights=nights
    )
    return current, maximum, availabilities_filtered, park_name, park_additional_information


# def generate_human_output(
#     info_by_park_id, start_date, end_date, gen_campsite_info=False
# ):
#     out = []
#     has_availabilities = False
#     for park_id, info in info_by_park_id.items():
#         current, maximum, available_dates_by_site_id, park_name = info
#         if current:
#             emoji = Emoji.SUCCESS.value
#             has_availabilities = True
#         else:
#             emoji = Emoji.FAILURE.value

#         out.append(
#             "{emoji} {park_name} ({park_id}): {current} site(s) available out of {maximum} site(s)".format(
#                 emoji=emoji,
#                 park_name=park_name,
#                 park_id=park_id,
#                 current=current,
#                 maximum=maximum,
#             )
#         )

#         # Displays campsite ID and availability dates.
#         if gen_campsite_info and available_dates_by_site_id:
#             for site_id, dates in available_dates_by_site_id.items():
#                 out.append(
#                     "  * Site {site_id} is available on the following dates:".format(
#                         site_id=site_id
#                     )
#                 )
#                 for date in dates:
#                     out.append(
#                         "    * {start} -> {end}".format(
#                             start=date["start"], end=date["end"]
#                         )
#                     )

#     if has_availabilities:
#         out.insert(
#             0,
#             "there are campsites available from {start} to {end}!!!".format(
#                 start=start_date.strftime(DateFormat.INPUT_DATE_FORMAT.value),
#                 end=end_date.strftime(DateFormat.INPUT_DATE_FORMAT.value),
#             ),
#         )
#     else:
#         out.insert(0, "There are no campsites available :(")
#     return "\n".join(out), has_availabilities


# def generate_json_output(info_by_park_id):
#     availabilities_by_park_id = {}
#     has_availabilities = False
#     for park_id, info in info_by_park_id.items():
#         current, _, available_dates_by_site_id, _ = info
#         if current:
#             has_availabilities = True
#             availabilities_by_park_id[park_id] = available_dates_by_site_id

#     return json.dumps(availabilities_by_park_id), has_availabilities


def getSiteInformation(info_by_site_id, site_id):
    # print("\ninfo_by_site_id=")
    # print(info_by_site_id)
    site_info=info_by_site_id[str(site_id)]
    return site_info

def generate_site_info_html(site_info):
    html=""
    # site
    # if "site" in site_info: html+=f" Site={site_info['site']}"
    # else: html+=f" Site=na"
    html+= " site=" + (site_info['site'] if ("site" in site_info) else "na")
    html+= " " + (site_info['loop'] if ("loop" in site_info) else "na")
    html+= ", max people=" + (str(site_info['max_num_people']) if ("max_num_people" in site_info) else "na")
    html+= ", capacity=" + (site_info['capacity_rating'] if ("capacity_rating" in site_info) else "na")
    html+= ", type=" + (site_info['campsite_type'] if ("campsite_type" in site_info) else "na")
    return html


def getOutputData(info_by_park_id):
    # transform tuple-based input data to JSON-based output data
    outputData=[]
    for park_id, info in info_by_park_id.items():
        current, maximum, available_dates_by_site_id, park_name, info_by_site_id = info
        campgroundData={
            "park_id":park_id, 
            "park_name":park_name, 
            "total_sites_count":maximum, 
            "available_sites_count":current,
            "available_sites":[]
            }
        for site_id, dates in available_dates_by_site_id.items():
            site_info=getSiteInformation(info_by_site_id, site_id)
            siteData={"site_id":site_id}
            siteData['site']=(site_info['site'] if ("site" in site_info) else "na")
            siteData['loop']=(site_info['loop'] if ("loop" in site_info) else "na")
            siteData['max_num_people']=(site_info['max_num_people'] if ("max_num_people" in site_info) else "na")
            siteData['capacity_rating']=(site_info['capacity_rating'] if ("capacity_rating" in site_info) else "na")
            siteData['campsite_type']=(site_info['campsite_type'] if ("campsite_type" in site_info) else "na")
            campgroundData['available_sites'].append(siteData)
        campgroundData['available_sites'] = sorted(campgroundData['available_sites'], key=lambda k: (k['loop'].lower(), k['site']))
        outputData.append(campgroundData)
        
        # need to sort 'available_sites" based on loop and site
    # need to sort outputData based on campground_name

    return outputData



# def generate_html_output_old(info_by_park_id, params):
#     html_prefix="<html><head></head><body>"
#     html_suffix="</body></html>"
#     html=""

#     startDateTxt=params['start_date'].strftime("%a %b %d, %Y") 
#     endDateTxt=params['end_date'].strftime("%a %b %d, %Y") 

#     html=f"Campsites for {startDateTxt} to {endDateTxt}"
#     html+="<br>"
#     for park_id, info in info_by_park_id.items():
#         # print("\ninfo=")
#         # print(info)
#         current, maximum, available_dates_by_site_id, park_name, info_by_site_id = info

#         html+="<p>"
#         html+=f"{park_name} <a href='https://www.recreation.gov/camping/campgrounds/{park_id}' target=_blank>recreation.gov</a>"
#         html+=f"<br>{current} sites available out of {maximum}"
#         html+="<br>"
#         for site_id, dates in available_dates_by_site_id.items():
#             site_info=getSiteInformation(info_by_site_id, site_id)
#             # print("/nSiteInfo=")
#             # print(site_info)

#             html+=f"<a href='https://www.recreation.gov/camping/campsites/{site_id}' target=_blank>{site_id}</a>"
#             html+=generate_site_info_html(site_info)
            
#             html+="<br>"
#         html+="</p>"


#     html=html_prefix+html+html_suffix
#     return html


def generate_html_output(data, params):
    html_prefix="<html><head></head><body>"
    html_suffix="</body></html>"
    html=""

    startDateTxt=params['start_date'].strftime("%a %b %d, %Y") 
    endDateTxt=params['end_date'].strftime("%a %b %d, %Y") 

    html=f"Campsites for {startDateTxt} to {endDateTxt}"
    html+="<br>"
    for park in data:
        html+="<p>"
        html+=f"{park['park_name']} <a href='https://www.recreation.gov/camping/campgrounds/{park['park_id']}' target=_blank>recreation.gov</a>"
        html+=f"<br>{park['available_sites_count']} sites available out of {park['total_sites_count']}"
        html+="<br>"

        for site in park['available_sites']:
            html+=f"<a href='https://www.recreation.gov/camping/campsites/{site['site_id']}' target=_blank>{site['site_id']}</a>"
            html+=f" site={site['loop']} {site['site']}"
            html+=f", max people={site['max_num_people']}"
            html+=f", capacity={site['capacity_rating']}"
            html+=f", type={site['campsite_type']}"
            html+="<br>"
        html+="</p>"


    html=html_prefix+html+html_suffix
    return html
        




def main():   # parks, json_output=False
    argList=sys.argv
    paramsFile=os.path.join('cfg_'+argList[1]+'.json')
    # print(paramsFile)


    if os.path.isfile(paramsFile):
        paramsTxt=open(paramsFile).read()
        params=json.loads(paramsTxt)
        params['start_date']=datetime.strptime(params['start_date'], '%Y-%m-%d')
        params['end_date']=datetime.strptime(params['end_date'], '%Y-%m-%d')
    
    print(f"params={params}")
    print('\n')
    
    info_by_park_id = {}
    for park_i in params['parks']:
        if park_i['check']:
            info_by_park_id[park_i['id']] = check_park(
                park_i['id'],
                params['start_date'],
                params['end_date'],
                '',
                '',
            )
 
    # restruct data to a JSON
    outputData=getOutputData(info_by_park_id)
    # print(outputData)


    # output_HTML=generate_html_output_old(info_by_park_id, params)
    # with open('output_old.html', 'w') as outfile:
    #     outfile.write(output_HTML)

    output_HTML=generate_html_output(outputData, params)
    with open('output.html', 'w') as outfile:
        outfile.write(output_HTML)
    
    # print('\n')
    # print(output)

    # if json_output:
    #     output, has_availabilities = generate_json_output(info_by_park_id)
    # else:
    #     output, has_availabilities = generate_human_output(
    #         info_by_park_id,
    #         params['start_date'],
    #         params['end_date'],
    #         True
    #     )
    # print(output)
    # return has_availabilities


if __name__ == "__main__":
    # parser = CampingArgumentParser()
    # args = parser.parse_args()
    # print(args)
    # print(type(args))

    # if args.params:
 
    # if args.debug:
    #     LOG.setLevel(logging.DEBUG)

    # LOG.setLevel(logging.DEBUG)

    main()
