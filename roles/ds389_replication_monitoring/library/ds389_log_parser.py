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
author:
    - Simon Pichugin (@droideck)
'''

EXAMPLES = '''
- name: Analyze replication logs and write to file
  dslog_parser:
    logfiles:
      - /var/log/dirsrv/slapd-replica1/access
      - /var/log/dirsrv/slapd-replica2/access
    anonymous: true
    output_file: /path/to/output_file.json
  register: replication_data
'''

from ansible.module_utils.basic import AnsibleModule
import datetime
import re
import json
import logging



class DSLogParser:
    REGEX_TIMESTAMP = re.compile(
        r'\[(?P<day>\d*)\/(?P<month>\w*)\/(?P<year>\d*):(?P<hour>\d*):(?P<minute>\d*):(?P<second>\d*)(\.(?P<nanosecond>\d*))+\s(?P<tz>[\+\-]\d*)'
    )
    REGEX_LINE = re.compile(
        r'\s(?P<quoted>[^= ]+="[^"]*")|(?P<var>[^= ]+=[^\s]+)|(?P<keyword>[^\s]+)'
    )
    MONTH_LOOKUP = {
        'Jan': "01", 'Feb': "02", 'Mar': "03", 'Apr': "04", 'May': "05", 'Jun': "06",
        'Jul': "07", 'Aug': "08", 'Sep': "09", 'Oct': "10", 'Nov': "11", 'Dec': "12"
    }

    class ParserResult:
        def __init__(self):
            self.keywords = []
            self.vars = {}
            self.raw = None
            self.timestamp = None

    def __init__(self, logname):
        self.logname = logname
        self.lineno = 0
        self.line = None

    def parse_timestamp(self, ts):
        """Parse a log's timestamp and convert it to a datetime object."""
        try:
            timedata = self.REGEX_TIMESTAMP.match(ts).groupdict()
        except AttributeError as e:
            logging.error(f'Failed to parse timestamp {ts} because of {e}')
            raise

        iso_ts = '{YEAR}-{MONTH}-{DAY}T{HOUR}:{MINUTE}:{SECOND}{TZH}:{TZM}'.format(
            YEAR=timedata['year'], MONTH=self.MONTH_LOOKUP[timedata['month']],
            DAY=timedata['day'], HOUR=timedata['hour'], MINUTE=timedata['minute'],
            SECOND=timedata['second'], TZH=timedata['tz'][0:3], TZM=timedata['tz'][3:5]
        )
        dt = datetime.datetime.fromisoformat(iso_ts)
        if timedata['nanosecond']:
            dt = dt.replace(microsecond=int(timedata['nanosecond']) // 1000)
        return dt

    def parse_line(self):
        l = self.line.split(']', 1)
        if len(l) != 2:
            return None
        result = self.REGEX_LINE.findall(l[1])
        if not result:
            return None

        r = self.ParserResult()
        r.timestamp = l[0] + "]"
        r.raw = result
        for (quoted, var, keyword) in result:
            if quoted:
                key, value = quoted.split('=', 1)
                r.vars[key] = value.strip('"')
            if var:
                key, value = var.split('=', 1)
                r.vars[key] = value
            if keyword:
                r.keywords.append(keyword)
        return r

    def action(self, r):
        print(f'{r.timestamp} {r.keywords} {r.vars}')

    def parse_file(self):
        """Parse the log file."""
        with open(self.logname, 'r') as f:
            for self.line in f:
                self.lineno += 1
                try:
                    r = self.parse_line()
                    if r:
                        self.action(r)
                except Exception as e:
                    logging.error(f"Skipping non-parsable line {self.lineno} ==> {self.line} ==> {e}")
                    raise


class ReplLag:
    def __init__(self, args):
        self.logfiles = args['logfiles']
        self.anonymous = args['anonymous']
        self.nbfiles = len(args['logfiles'])
        self.csns = {}
        self.start_udt = None  
        self.start_dt = None  

    class Parser(DSLogParser):
        def __init__(self, idx, logfile, result):
            super().__init__(logfile)
            self.result = result
            self.idx = idx

        def action(self, r):
            try:
                csn = r.vars['csn']
                dt = self.parse_timestamp(r.timestamp)
                udt = dt.astimezone(datetime.timezone.utc).timestamp()
                if self.result.start_udt is None or self.result.start_udt > udt:
                    self.result.start_udt = udt
                    self.result.start_dt = dt
                if csn not in self.result.csns:
                    self.result.csns[csn] = {}
                record = {"logtime": udt, "etime": r.vars['etime']}
                self.result.csns[csn][self.idx] = record
            except KeyError:
                pass

    def parse_files(self):
        """Parse all log files."""
        for idx, f in enumerate(self.logfiles):
            parser = self.Parser(idx, f, self)
            parser.parse_file()

    def build_result(self):
        """Build the result object for Ansible."""
        obj = {
            "start-time": str(self.start_dt),
            "utc-start-time": self.start_udt,
            "utc-offset": self.start_dt.utcoffset().total_seconds(),
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
            logfiles=dict(required=True, type='list', elements='path'),
            anonymous=dict(required=False, type='bool', default=False),
            output_file=dict(required=True, type='str')
        ),
        supports_check_mode=True
    )

    log_parser = ReplLag(module.params)
    log_parser.parse_files()
    result = log_parser.build_result()

    # Write the result to the specified output file in JSON format
    output_file_path = module.params['output_file']
    try:
        with open(output_file_path, 'w') as output_file:
            json.dump(result, output_file, indent=4)
    except Exception as e:
        module.fail_json(msg=f"Failed to write to output file {output_file_path}: {e}")

    module.exit_json(changed=False, message=f"Replication data written to {output_file_path}")


if __name__ == "__main__":
    main()
