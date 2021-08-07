#!/usr/bin/env python3

import argparse
import json

parser = argparse.ArgumentParser(description='Find available IPs')
parser.add_argument('--taken_ips', type=str, help='Already taken list of IP addresses')
parser.add_argument('--network_id', type=str, help='Network ID', default='10.10.10')
parser.add_argument('--starting_id', type=int, help='Starting host ID', default=20)
parser.add_argument('-n', type=int, help='Number of IPs to find', default=1)

args = parser.parse_args()

remove_unneeded_chars = str.maketrans('', '', "[]'")

parsed_ips = args.taken_ips.translate(remove_unneeded_chars).split(',')
parsed_ips = [ip.strip() for ip in parsed_ips]
parsed_ips = [ip for ip in parsed_ips if ip != ""]

taken_hosts_ids = set([int(ip.split('.')[-1]) for ip in parsed_ips])
possible_ids = set(range(args.starting_id, 255))
available_ids = sorted(list(possible_ids - taken_hosts_ids))

available_host_ips = [f'{args.network_id}.{available_id}' for available_id in available_ids[:args.n]]

print(json.dumps({"ips": available_host_ips}))
