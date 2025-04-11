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
    - Uses lib389.repltools.ReplicationLogAnalyzer for accurate replication analysis.
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
        required: false
        type: str
    png_output_path:
        description:
            - Path where the plot image should be saved.
            - If not specified, no PNG file will be created.
        required: false
        type: str
    html_output_path:
        description:
            - Path where the interactive plot HTML file should be saved.
            - If not specified, no HTML file will be created.
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
  ds389_logs_plot:
    input: "/path/to/log_data.json"
    csv_output_path: "/path/to/output_data.csv"
    png_output_path: "/path/to/plot.png"
    html_output_path: "/path/to/interactive_plot.html"
    only_fully_replicated: yes
    lag_time_lowest: 10
    utc_offset: -3600
    repl_lag_threshold: 5
    start_time: "2024-01-01 00:00:00"
    end_time: "2024-01-02 00:00:00"

- name: Generate 389 DS log data CSV with minimap etime and only not replicated changes
  ds389_logs_plot:
    input: "/path/to/log_data.json"
    csv_output_path: "/path/to/output_data.csv"
    only_not_replicated: yes
    etime_lowest: 2.5
'''

from ansible.module_utils.basic import AnsibleModule
import datetime
import json
import os
import logging
import tempfile
from datetime import datetime, timezone, timedelta

try:
    from lib389.repltools import ReplicationLogAnalyzer
    LIB389_AVAILABLE = True
except ImportError:
    LIB389_AVAILABLE = False
    logging.getLogger(__name__).warning("lib389 is not available. Using fallback implementation.")


class InputAdapter:
    """
    Adapter class to convert merged JSON data from Ansible format to a format compatible with
    lib389.repltools.ReplicationLogAnalyzer
    """
    def __init__(self, input_path):
        self.input_path = input_path

    def prepare_log_dirs(self):
        """
        Prepare virtual log directories from the merged JSON data.
        Returns a list of virtual log directories for ReplicationLogAnalyzer.
        """
        try:
            with open(self.input_path, "r") as file:
                data = json.load(file)

            # Create a temporary directory for each "server" in the JSON
            server_logs = {}
            temp_dirs = []

            for idx, server_info in enumerate(data.get('log-files', [])):
                # Create a temporary directory for this server's logs
                temp_dir = tempfile.mkdtemp(prefix=f"ds389_repl_log_analyzer_{idx}_")
                temp_dirs.append(temp_dir)
                server_logs[idx] = temp_dir

            # Process CSNs and write virtual log files
            log_dirs = list(server_logs.values())
            return log_dirs, data

        except Exception as e:
            raise RuntimeError(f"Failed to parse input file: {str(e)}")


def convert_to_timezone(dt_string, utc_offset_seconds=0):
    """Convert date string to timezone-aware datetime"""
    try:
        dt = datetime.strptime(dt_string, "%Y-%m-%d %H:%M:%S")
        tz = timezone(timedelta(seconds=utc_offset_seconds))
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None


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

    if not LIB389_AVAILABLE:
        module.fail_json(msg="This module requires lib389. Please install the python3-lib389 package.")

    input_path = module.params['input']
    if not os.path.exists(input_path):
        module.fail_json(msg=f"Input file {input_path} not found")

    try:
        # Create output directory if needed
        output_dir = os.path.dirname(module.params.get('csv_output_path', ''))
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        # Read the input file
        with open(input_path, 'r') as f:
            json_data = json.load(f)

        # Set up the analyzer parameters
        suffixes = []
        log_dirs = []

        # Create a temporary directory for each server in the logs
        temp_base = tempfile.mkdtemp(prefix="ds389_repl_analysis_")
        for i, logfile in enumerate(json_data.get('log-files', [])):
            server_dir = os.path.join(temp_base, f"server_{i}")
            os.makedirs(server_dir, exist_ok=True)
            # Create a virtual "access" log file that the analyzer will read
            with open(os.path.join(server_dir, "access"), 'w') as f:
                f.write("# Virtual log file for ReplicationLogAnalyzer\n")
            log_dirs.append(server_dir)

        # Now process CSNs and convert to the format lib389 expects
        csns = json_data.get('lag', {})

        # Parse time range parameters
        time_range = {}
        utc_offset_seconds = module.params.get('utc_offset', 0)

        if module.params.get('start_time'):
            time_range['start'] = convert_to_timezone(
                module.params['start_time'],
                utc_offset_seconds
            )

        if module.params.get('end_time'):
            time_range['end'] = convert_to_timezone(
                module.params['end_time'],
                utc_offset_seconds
            )

        # Create the analyzer instance
        analyzer = ReplicationLogAnalyzer(
            log_dirs=log_dirs,
            suffixes=suffixes,  # Auto-detect suffixes from the logs
            anonymous=True,  # Always anonymize for consistent naming
            only_fully_replicated=module.params['only_fully_replicated'],
            only_not_replicated=module.params['only_not_replicated'],
            lag_time_lowest=module.params['lag_time_lowest'],
            etime_lowest=module.params['etime_lowest'],
            repl_lag_threshold=module.params['repl_lag_threshold'],
            utc_offset=utc_offset_seconds,
            time_range=time_range
        )

        # Instead of parsing the logs, we'll directly set the CSN data
        # This is needed because we already have the parsed data in Ansible format
        analyzer.csns = csns

        # Determine the report formats
        formats = []
        if module.params.get('csv_output_path'):
            formats.append('csv')
        if module.params.get('png_output_path'):
            formats.append('png')
        if module.params.get('html_output_path'):
            formats.append('html')

        if not formats:
            module.fail_json(msg="No output formats specified (csv, png, or html)")

        # Generate the reports
        report_name = "replication_analysis"
        try:
            generated_files = analyzer.generate_report(
                output_dir=output_dir,
                formats=formats,
                report_name=report_name
            )

            # Rename the files to match expected output paths
            if 'csv' in generated_files and module.params.get('csv_output_path'):
                os.rename(generated_files['csv'], module.params['csv_output_path'])

            if 'png' in generated_files and module.params.get('png_output_path'):
                os.rename(generated_files['png'], module.params['png_output_path'])

            if 'html' in generated_files and module.params.get('html_output_path'):
                os.rename(generated_files['html'], module.params['html_output_path'])

            # Clean up temporary files
            import shutil
            shutil.rmtree(temp_base, ignore_errors=True)

            module.exit_json(
                changed=True,
                message="Plot generated successfully",
                files=generated_files
            )

        except Exception as e:
            module.fail_json(msg=f"Failed to generate reports: {str(e)}")

    except Exception as e:
        module.fail_json(msg=f"Failed to process file {input_path}: {str(e)}")


if __name__ == '__main__':
    main()
