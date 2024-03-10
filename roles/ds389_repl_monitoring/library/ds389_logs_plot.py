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
module: ds389_logs_plot
short_description: Plots 389 Directory Server log data from JSON file
description:
    - This module processes a JSON file containing 389 Directory Server (DS) log data to plot replication lag and execution time (etime) over time.
options:
    input:
        description:
            - Path to the input JSON file containing the log data.
        required: true
        type: str
    csv_output_path:
        description:
            - Path where the CSV file should be generated.
            - If not specified, no CSV file will be created.
        required: true
        type: str
    png_output_path:
        description:
            - Path where the plot image should be saved.
            - If not specified, the plot will be saved to a temporary file and its path returned.
        required: false
        type: str
    only_fully_replicated:
        description:
            - Filter to show only changes replicated on all replicas.
        required: false
        type: bool
        default: false
    only_not_replicated:
        description:
            - Filter to show only changes not replicated on all replicas.
        required: false
        type: bool
        default: false
    lag_time_lowest:
        description:
            - Filter to show only changes with lag time greater than or equal to the specified value.
        required: false
        type: float
    etime_lowest:
        description:
            - Filter to show only changes with execution time (etime) greater than or equal to the specified value.
        required: false
        type: float
    utc_offset:
        description:
            - UTC offset in seconds for timezone adjustment.
        required: false
        type: int
    repl_lag_threshold:
        description:
            - Replication monitoring threshold value.
            - A horizontal line will be drawn in the plot to represent this threshold.
        required: false
        type: float
author:
    - Simon Pichugin (@droideck)
'''

EXAMPLES = '''
- name: Plot 389 DS log data and generate CSV
  dslogs_plot:
    input: "/path/to/log_data.json"
    csv_output_path: "/path/to/output_data.csv"
    png_output_path: "/path/to/plot.png"
    only_fully_replicated: yes
    lag_time_lowest: 10
    utc_offset: -3600
    repl_lag_threshold: 5

- name: Generate 389 DS log data CSV with minimap etime and only not replicated changes
  dslogs_plot:
    input: "/path/to/log_data.json"
    csv_output_path: "/path/to/output_data.csv"
    only_not_replicated: yes
    etime_lowest: 2.5
'''

from ansible.module_utils.basic import AnsibleModule
import datetime
import json
import matplotlib.pyplot as plt
import os

class CsnInfo:
    def __init__(self, csn):
        self.csn = csn
        self.oldest_time = None
        self.lag_time = None
        self.etime = None
        self.replicated_on = {}

    def json_parse(self, idx, json_dict):
        self.replicated_on[idx] = json_dict
        udt = json_dict['logtime']
        etime = float(json_dict['etime'])
        self._update_times(udt, etime, idx)

    def _update_times(self, udt, etime, idx):
        if self.oldest_time is None or self.oldest_time[0] > udt:
            self.oldest_time = [udt, idx]
        if self.lag_time is None or self.lag_time[0] < udt:
            self.lag_time = [udt, idx]
        if self.etime is None or self.etime[0] < etime:
            self.etime = [etime, idx]

    def resolve(self):
        self.lag_time[0] -= self.oldest_time[0]

    def _to_dict(self):
        return {
            'csn': self.csn,
            'lag_time': self.lag_time,
            'etime': self.etime,
            'replicated_on': self.replicated_on
        }

class LagInfo:
    def __init__(self, module_params):
        self.module_params = module_params
        self.utc_offset = None
        self.log_files = None
        self.tz = None
        self.lag = []
        self.index_list = []
        self._setup_timezone()

    def _setup_timezone(self):
        if self.module_params['utc_offset'] is not None:
            tz_delta = datetime.timedelta(seconds=self.module_params['utc_offset'])
            self.tz = datetime.timezone(tz_delta)

    def date_from_udt(self, udt):
        try:
            return datetime.datetime.fromtimestamp(udt, tz=self.tz)
        except TypeError:
            return "?"

    def is_filtered(self, csninfo):
        if self.module_params['only_fully_replicated'] and len(self.index_list) != len(csninfo.replicated_on):
            return True
        if self.module_params['only_not_replicated'] and len(self.index_list) == len(csninfo.replicated_on):
            return True
        if self.module_params['lag_time_lowest'] and csninfo.lag_time[0] <= self.module_params['lag_time_lowest']:
            return True
        if self.module_params['etime_lowest'] and csninfo.etime[0] <= self.module_params['etime_lowest']:
            return True
        return False

    def json_parse(self, fd):
        json_dict = json.load(fd)
        self.utc_offset = json_dict["utc-offset"]
        self.log_files = json_dict["log-files"]
        self.index_list = list(range(len(self.log_files)))
        self._setup_timezone()
        for csn, csninfo in json_dict['lag'].items():
            info = CsnInfo(csn)
            for idx, record in csninfo.items():
                idx = int(idx)
                if idx in self.index_list:
                    info.json_parse(idx, record)
            info.resolve()
            if not self.is_filtered(info):
                self.lag.append(info)

    def plot_lag_image(self, module):
        self.lag.sort(key=lambda csninfo: csninfo.oldest_time[0])
        starting_time = self.date_from_udt(self.lag[0].oldest_time[0])

        xdata = [self.date_from_udt(i.oldest_time[0]) for i in self.lag]
        ydata = [i.lag_time[0] for i in self.lag]
        edata = [i.etime[0] for i in self.lag]

        if self.module_params['csv_output_path']:
            try:
                with open(self.module_params['csv_output_path'], "w", encoding="utf-8") as csv_file:
                    csv_file.write("timestamp,lag,etime\n")
                    for idx in range(len(xdata)):
                        timestamp = xdata[idx].strftime('%Y-%m-%d %H:%M:%S') if xdata[idx] != "?" else "?"
                        csv_file.write(f"{timestamp},{ydata[idx]},{edata[idx]}\n")
            except Exception as e:
                module.fail_json(msg=f"Failed to write CSV file {self.module_params['csv_output_path']}: {e}")

        plt.plot(xdata, ydata, label='lag')
        plt.plot(xdata, edata, label='etime')

        if self.module_params['repl_lag_threshold'] is not None:
            plt.axhline(y=self.module_params['repl_lag_threshold'], color='r', linestyle='-', label='Replication Lag Threshold')

        plt.title('Replication lag time')
        plt.ylabel('time (s)')
        plt.xlabel(f'log time (starting on {starting_time})')
        plt.legend()

        if self.module_params['csv_output_path']:
            message = f"CSV data saved to {self.module_params['csv_output_path']}"
        else:
            module.fail_json(msg=f"CSV path (csv_output_path:) is requred")

        if self.module_params['png_output_path']:
            plt.savefig(self.module_params['png_output_path'])
            message += f" And plot saved to {self.module_params['png_output_path']}"
        module.exit_json(changed=True, message=message)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            input=dict(type='str', required=True),
            csv_output_path=dict(type='str', required=False),
            png_output_path=dict(type='str', required=False),
            only_fully_replicated=dict(type='bool', default=False),
            only_not_replicated=dict(type='bool', default=False),
            lag_time_lowest=dict(type='float', required=False),
            etime_lowest=dict(type='float', required=False),
            utc_offset=dict(type='int', required=False),
            repl_lag_threshold=dict(type='float', required=False)
        ),
        supports_check_mode=True
    )

    input_path = module.params['input']
    if not os.path.exists(input_path):
        module.fail_json(msg=f"Input file {input_path} not found")

    try:
        with open(input_path, "r", encoding="utf-8") as fd:
            lag_info = LagInfo(module.params)
            lag_info.json_parse(fd)
            lag_info.plot_lag_image(module)
    except Exception as e:
        module.fail_json(msg=f"Failed to process file {input_path}: {e}")

if __name__ == '__main__':
    main()
