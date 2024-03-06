#!/usr/bin/python3
# --- BEGIN COPYRIGHT BLOCK ---
# Copyright (C) 2024 Red Hat, Inc.
# All rights reserved.
#
# License: GPL (version 3 or any later version).
# See LICENSE for details.
# --- END COPYRIGHT BLOCK ---

DOCUMENTATION = '''
---
module: ds389_merge_logs
short_description: Merges multiple JSON log files into a single file.
description:
    - This module takes multiple JSON log files and merges them into a single JSON file.
    - It is designed to be used in situations where log data from multiple sources needs to be combined for analysis or graphing purposes.
options:
    files:
        description:
            - A list of paths to the JSON files to be merged.
        required: true
        type: list
    output:
        description:
            - The path to the output file where the merged JSON will be saved.
        required: true
        type: str
author:
    - Simon Pichugin (@droideck)
'''

EXAMPLES = '''
---
- name: Merge JSON logs
  ds389_merge_logs:
    files:
      - "/path/to/log1.json"
      - "/path/to/log2.json"
      - "/path/to/log3.json"
    output: "/path/to/merged_log.json"
  register: merge_result

- name: Display merge result
  debug:
    msg: "{{ merge_result.message }}"

- name: Merge JSON logs with dynamic list
  ds389_merge_logs:
    files: "{{ list_of_logs }}"
    output: "/path/to/dynamic_merged_log.json"
  register: dynamic_merge_result

- name: Display dynamic merge result
  debug:
    msg: "{{ dynamic_merge_result.message }}"
'''


from ansible.module_utils.basic import AnsibleModule
import json
from datetime import datetime


def merge_jsons(json_list):
    earliest_json = min(json_list, key=lambda x: datetime.fromisoformat(x["start-time"]))
    merged_json = earliest_json.copy()
    merged_json["log-files"] = []
    merged_json["lag"] = {}

    for json_data in json_list:
        merged_json["log-files"].extend(json_data["log-files"])

    for idx, json_data in enumerate(json_list):
        for key, value in json_data["lag"].items():
            if key not in merged_json["lag"]:
                merged_json["lag"][key] = {}
            for inner_key, inner_value in value.items():
                merged_json["lag"][key][str(idx)] = inner_value

    return merged_json

def split_json(input_json):
    data = json.loads(input_json)
    common_fields = {key: data[key] for key in data if key not in ['log-files', 'lag']}
    json_outputs = []

    for log_file in data['log-files']:
        json_obj = common_fields.copy()
        json_obj['log-files'] = [log_file]
        json_obj['lag'] = {}
        json_outputs.append(json_obj)

    for lag_id, lag_info in data['lag'].items():
        file_index = int(list(lag_info.keys())[0])
        json_outputs[file_index]['lag'][lag_id] = {"0": lag_info[str(file_index)]}

    return json_outputs


def process_file(file_path, module):
    try:
        with open(file_path, 'r') as file:
            input_json = file.read()
        return split_json(input_json)
    except Exception as e:
        module.fail_json(msg=f"Failed to read {file_path}: {str(e)}")

def main():
    module = AnsibleModule(
        argument_spec=dict(
            files=dict(type='list', required=True),
            output=dict(type='str', required=True)
        ),
        supports_check_mode=True
    )

    files = module.params['files']
    output = module.params['output']

    try:
        json_processed_list = []
        for file_path in files:
            splitted_jsons = process_file(file_path, module)
            for json_obj in splitted_jsons:
                json_processed_list.append(json_obj)
        merged_result = merge_jsons(json_processed_list)

        with open(output, 'w') as outfile:
            json.dump(merged_result, outfile, indent=4)
        module.exit_json(changed=True, message="JSON merged successfully")

    except Exception as e:
        module.fail_json(msg=str(e))

if __name__ == '__main__':
    main()
