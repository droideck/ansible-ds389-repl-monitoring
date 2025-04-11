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
module: ds389_log_parser
short_description: Parse 389 Directory Server access logs and calculate replication lags
description:
     - This module processes 389 Directory Server access log files from multiple replicas to identify replication lags.
options:
  logfiles:
    description:
      - List of paths to 389ds access log files.
    required: true
    type: list
    elements: path
  anonymous:
    description:
      - Replace log file names with generic identifiers.
    required: false
    type: bool
    default: false
  output_file:
    description:
      - Path to the output file where the results will be written.
    required: true
    type: str
  server_name:
    description:
      - Name of the server to identify log records.
    required: true
    type: str
author:
    - Simon Pichugin (@droideck)
'''

EXAMPLES = '''
- name: Analyze replication logs and write to file
  ds389_log_parser:
    server_name: "supplier1"
    logfiles:
      - /var/log/dirsrv/slapd-replica1/access
      - /var/log/dirsrv/slapd-replica2/access
    anonymous: true
    output_file: /path/to/output_file.json
'''

from ansible.module_utils.basic import AnsibleModule
import datetime
import json
import logging

try:
    from lib389.repltools import DSLogParser
    LIB389_AVAILABLE = True
except ImportError:
    LIB389_AVAILABLE = False
    logging.getLogger(__name__).warning("lib389 is not available. Using fallback implementation.")


class ReplLag:
    def __init__(self, args):
        self.server_name = args['server_name']
        self.logfiles = args['logfiles']
        self.anonymous = args['anonymous']
        self.csns = {}
        self.start_udt = None
        self.start_dt = None

    def parse_with_lib389(self):
        """Parse all log files"""
        for logfile in self.logfiles:
            # Initialize the DSLogParser with the logfile
            # We don't filter by suffix at this stage - we collect all CSNs
            parser = DSLogParser(logname=logfile, suffixes=[], batch_size=1000)

            # Process parsed records
            for record in parser.parse_file():
                # Skip records without CSN or suffix
                if not record.get('csn'):
                    continue

                csn = record.get('csn')
                timestamp = record.get('timestamp')

                # Convert to UTC timestamp
                if isinstance(timestamp, datetime.datetime):
                    udt = timestamp.astimezone(datetime.timezone.utc).timestamp()
                else:
                    continue

                # Track earliest timestamp
                if self.start_udt is None or self.start_udt > udt:
                    self.start_udt = udt
                    self.start_dt = timestamp

                # Add CSN record
                if csn not in self.csns:
                    self.csns[csn] = {}

                # Store data
                etime = record.get('etime', 0)
                # Note: we use index 0 since we're on a single server (will be remapped during merge)
                self.csns[csn][0] = {
                    "logtime": udt,
                    "etime": str(etime) if etime is not None else "0",
                    "server_name": self.server_name,
                    "suffix": record.get('suffix'),
                    "target_dn": record.get('target_dn')
                }

    def build_result(self):
        """Build the result object for Ansible."""
        obj = {
            "start-time": str(self.start_dt) if self.start_dt else str(datetime.datetime.now(datetime.timezone.utc)),
            "utc-start-time": self.start_udt if self.start_udt else datetime.datetime.now(datetime.timezone.utc).timestamp(),
            "utc-offset": self.start_dt.utcoffset().total_seconds() if self.start_dt and self.start_dt.utcoffset() else 0,
            "lag": self.csns
        }

        if self.anonymous:
            obj['log-files'] = list(range(len(self.logfiles)))
        else:
            obj['log-files'] = self.logfiles

        return obj


def main():
    module = AnsibleModule(
        argument_spec=dict(
            server_name=dict(required=True, type='str'),
            logfiles=dict(required=True, type='list', elements='path'),
            anonymous=dict(required=False, type='bool', default=False),
            output_file=dict(required=True, type='str')
        ),
        supports_check_mode=True
    )

    # Check if lib389 is available
    if not LIB389_AVAILABLE:
        module.fail_json(msg="This module requires lib389. Please install the python3-lib389 package.")

    log_parser = ReplLag(module.params)

    try:
        log_parser.parse_with_lib389()
        result = log_parser.build_result()

        # Write the result to the specified output file in JSON format
        output_file_path = module.params['output_file']
        try:
            with open(output_file_path, 'w') as output_file:
                json.dump(result, output_file, indent=4)
        except Exception as e:
            module.fail_json(msg=f"Failed to write to output file {output_file_path}: {e}")

        module.exit_json(changed=False, message=f"Replication data written to {output_file_path}")
    except Exception as e:
        module.fail_json(msg=f"Failed to parse log files: {str(e)}")


if __name__ == "__main__":
    main()