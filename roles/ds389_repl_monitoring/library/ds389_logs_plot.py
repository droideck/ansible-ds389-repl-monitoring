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
    start_time:
        description:
            - Start time for the time range filter in 'YYYY-MM-DD HH:MM:SS' format.
        required: false
        type: str
    end_time:
        description:
            - End time for the time range filter in 'YYYY-MM-DD HH:MM:SS' format.
        required: false
        type: str
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
    start_time: "2024-01-01 00:00:00"
    end_time: "2024-01-02 00:00:00"

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
import plotly.graph_objs as go
import plotly.io as pio

class CsnInfo:
    def __init__(self, csn, tz):
        self.csn = csn
        self.tz = tz
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
        if self.oldest_time is not None and self.lag_time is not None:
            self.lag_time[0] -= self.oldest_time[0]

    def _to_dict(self):
        return {
            'csn': self.csn,
            'lag_time': self.lag_time,
            'etime': self.etime,
            'replicated_on': self.replicated_on
        }

    def describe_csn(self):
        try:
            timestamp_hex = self.csn[:8]
            timestamp = datetime.datetime.utcfromtimestamp(int(timestamp_hex, 16)).astimezone(self.tz).strftime('%Y-%m-%d %H:%M:%S')
            sequence_number = int(self.csn[8:12], 16)
            identifier = int(self.csn[12:16], 16)
            sub_sequence_number = int(self.csn[16:20], 16)
            return f"{timestamp} | Sequence: {sequence_number} | ID: {identifier} | Sub-sequence: {sub_sequence_number}"
        except Exception as e:
            return f"Failed to describe CSN: {e}"

class LagInfo:
    def __init__(self, module_params):
        self.module_params = module_params
        self.utc_offset = None
        self.log_files = None
        self.tz = None
        self.lag = []
        self.index_list = []
        self._setup_timezone()
        self.start_time = self._parse_time(module_params.get('start_time', '1970-01-01 00:00:00'))
        self.end_time = self._parse_time(module_params.get('end_time', '9999-12-31 23:59:59'))

    def _setup_timezone(self):
        if self.module_params['utc_offset'] is not None:
            tz_delta = datetime.timedelta(seconds=self.module_params['utc_offset'])
            self.tz = datetime.timezone(tz_delta)
        else:
            self.tz = datetime.timezone.utc

    def _parse_time(self, time_str):
        try:
            return datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=self.tz)
        except ValueError:
            raise ValueError(f"Time format should be 'YYYY-MM-DD HH:MM:SS', but got '{time_str}'")

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
        if self.start_time and self.date_from_udt(csninfo.oldest_time[0]) < self.start_time:
            return True
        if self.end_time and self.date_from_udt(csninfo.oldest_time[0]) > self.end_time:
            return True
        return False

    def json_parse(self, fd):
        json_dict = json.load(fd)
        self.utc_offset = json_dict["utc-offset"]
        self.log_files = json_dict["log-files"]
        self.index_list = list(range(len(self.log_files)))
        self._setup_timezone()
        for csn, csninfo in json_dict['lag'].items():
            info = CsnInfo(csn, self.tz)
            for idx, record in csninfo.items():
                idx = int(idx)
                if idx in self.index_list:
                    info.json_parse(idx, record)
            info.resolve()
            if not self.is_filtered(info):
                self.lag.append(info)

    def plot_lag_image(self, module):
        if not self.lag:
            module.fail_json(msg="No data available to plot.")

        self.lag.sort(key=lambda csninfo: csninfo.oldest_time[0])
        starting_time = self.date_from_udt(self.lag[0].oldest_time[0])

        xdata = [self.date_from_udt(i.oldest_time[0]) for i in self.lag]
        ydata = [i.lag_time[0] for i in self.lag]
        edata = [i.etime[0] for i in self.lag]

        if self.module_params['csv_output_path']:
            try:
                with open(self.module_params['csv_output_path'], "w", encoding="utf-8") as csv_file:
                    csv_file.write("timestamp,lag,etime,csn,described_csn\n")
                    for idx in range(len(xdata)):
                        timestamp = xdata[idx].strftime('%Y-%m-%d %H:%M:%S') if xdata[idx] != "?" else "?"
                        described_csn = self.lag[idx].describe_csn()
                        csv_file.write(f"{timestamp},{ydata[idx]},{edata[idx]},{self.lag[idx].csn},{described_csn}\n")
            except Exception as e:
                module.fail_json(msg=f"Failed to write CSV file {self.module_params['csv_output_path']}: {e}")

        plt.figure(figsize=(15, 7))  # Set the figure size to be wide
        plt.plot(xdata, ydata, label='Replication Lag', color='blue', linestyle='-', linewidth=1.5, marker='o')
        plt.plot(xdata, edata, label='Elapsed Time', color='green', linestyle='-', linewidth=1.5, marker='x')

        if self.module_params['repl_lag_threshold'] != 0:
            plt.axhline(y=self.module_params['repl_lag_threshold'], color='red', linestyle='-', label='Replication Lag Threshold')

        plt.title('Replication Lag Time', fontsize=16)
        plt.ylabel('Time (s)', fontsize=14)
        plt.xlabel(f'Log Time (starting on {starting_time})', fontsize=14)
        plt.xticks(rotation=45)
        plt.legend(loc='upper right', fontsize=12)
        plt.grid(True)
        plt.tight_layout()

        if self.module_params['png_output_path']:
            plt.savefig(self.module_params['png_output_path'])
            module.exit_json(changed=True, message=f"CSV data saved to {self.module_params['csv_output_path']}. Plot saved to {self.module_params['png_output_path']}")
        else:
            module.fail_json(msg="PNG output path (png_output_path) is required")


    def plot_interactive_html(self, module):
        if not self.lag:
            module.fail_json(msg="No data available to plot.")

        self.lag.sort(key=lambda csninfo: csninfo.oldest_time[0])
        starting_time = self.date_from_udt(self.lag[0].oldest_time[0])

        xdata = [self.date_from_udt(i.oldest_time[0]) for i in self.lag]
        ydata = [i.lag_time[0] for i in self.lag]
        edata = [i.etime[0] for i in self.lag]
        csn_hover_text = [f"CSN: {i.csn}<br>Described: {i.describe_csn()}<br>Time: {xdata[idx].strftime('%Y-%m-%d %H:%M:%S')}" for idx, i in enumerate(self.lag)]

        trace1 = go.Scatter(x=xdata, y=ydata, mode='lines+markers', name='Replication Lag', text=csn_hover_text, hoverinfo='text+x+y')
        trace2 = go.Scatter(x=xdata, y=edata, mode='lines+markers', name='Elapsed Time', text=csn_hover_text, hoverinfo='text+x+y')

        layout = go.Layout(
            title='Replication Lag Time',
            xaxis=dict(title=f'Log Time (starting on {starting_time})'),
            yaxis=dict(title='Time (s)'),
            hovermode='closest',
            shapes=[{
                'type': 'line',
                'x0': xdata[0],
                'x1': xdata[-1],
                'y0': self.module_params['repl_lag_threshold'],
                'y1': self.module_params['repl_lag_threshold'],
                'line': {
                    'color': 'red',
                    'width': 2,
                    'dash': 'dash',
                },
                'name': 'Replication Lag Threshold'
            }] if self.module_params['repl_lag_threshold'] != 0 else [],
            margin=dict(l=40, r=30, b=80, t=100),
            annotations=[
                dict(
                    x=0.5,
                    y=-0.15,
                    showarrow=False,
                    text="Click on a point to copy the CSN to clipboard",
                    xref="paper",
                    yref="paper",
                    xanchor="center",
                    yanchor="auto",
                    font=dict(size=12)
                )
            ]
        )

        fig = go.Figure(data=[trace1, trace2], layout=layout)

        html_content = pio.to_html(fig, full_html=False, include_plotlyjs='cdn')
        custom_js = """
        <script>
        document.addEventListener("DOMContentLoaded", function() {
            var plot = document.getElementsByClassName('plotly-graph-div')[0];
            plot.on('plotly_click', function(data) {
                var infotext = data.points.map(function(d) {
                    return d.text;
                });
                var csn = infotext[0].replace('CSN: ', '').split('<br>')[0];
                navigator.clipboard.writeText(csn).then(function() {
                    alert('CSN ' + csn + ' copied to clipboard');
                }, function(err) {
                    console.error('Could not copy text: ', err);
                });
            });
        });
        </script>
        """

        full_html = f"<!DOCTYPE html><html><head><title>Replication Lag Time</title></head><body><div id='plotly-div'>{html_content}</div>{custom_js}</body></html>"

        if self.module_params['html_output_path']:
            try:
                with open(self.module_params['html_output_path'], 'w', encoding='utf-8') as f:
                    f.write(full_html)
                module.exit_json(changed=True, message=f"CSV data saved to {self.module_params['csv_output_path']}. Interactive plot saved to {self.module_params['html_output_path']}")
            except Exception as e:
                module.fail_json(msg=f"Failed to write HTML file {self.module_params['html_output_path']}: {e}")
        else:
            module.fail_json(msg="HTML output path (html_output_path) is required")


def main():
    module = AnsibleModule(
        argument_spec=dict(
            input=dict(type='str', required=True),
            csv_output_path=dict(type='str', required=False),
            png_output_path=dict(type='str', required=False),
            html_output_path=dict(type='str', required=False),
            only_fully_replicated=dict(type='bool', default=False),
            only_not_replicated=dict(type='bool', default=False),
            lag_time_lowest=dict(type='float', required=False),
            etime_lowest=dict(type='float', required=False),
            utc_offset=dict(type='int', required=False),
            repl_lag_threshold=dict(type='float', required=False),
            start_time=dict(type='str', required=False, default='1970-01-01 00:00:00'),
            end_time=dict(type='str', required=False, default='9999-12-31 23:59:59')
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
            try:
                if module.params['html_output_path']:
                    lag_info.plot_interactive_html(module)
                else:
                    lag_info.plot_lag_image(module)
            except IndexError:
                module.fail_json(msg="There's no data to include in the report")
    except Exception as e:
        module.fail_json(msg=f"Failed to process file {input_path}: {e}")

if __name__ == '__main__':
    main()
