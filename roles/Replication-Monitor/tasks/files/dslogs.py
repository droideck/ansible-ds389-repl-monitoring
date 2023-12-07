#!/usr/bin/python3
# --- BEGIN COPYRIGHT BLOCK ---
# Copyright (C) 2023 Red Hat, Inc.
# All rights reserved.
#
# License: GPL (version 3 or any later version).
# See LICENSE for details.
# --- END COPYRIGHT BLOCK ---

import datetime
import sys
import re
import argparse
import json
import logging

USAGE = """
Tools to parse 389ds access log files from different replicas
to extract interesting data (like csn, etime, and event time) and store it in a json file
Usage:
   dslogs replica1_logfile replica2_logfile > output.json
"""


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
    def __init__(self, args, fd):
        self.logfiles = args.logfiles
        self.nbfiles = len(args.logfiles)
        self.args = args
        self.fd = fd
        # csns is a dict { csn : { "oldest_logtime": udt, "records" : { log_index {[ lag , etime, file-index, utc-log-time, utc-csb-oldest-log-time ] }
        #  lag = '*' means that the csn has not been seen in that log file
        self.csns = {}         # Dict for CSNs
        self.start_udt = None  # Oldest time seen in log files (UTC timestamp)
        self.start_dt = None   # Oldest time seen in log files (datetime object)

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

    def jsonprint(self):
        """Print the collected data in JSON format."""
        obj = {
            "# comment": "dslog output (replication lags data)",
            "start-time": str(self.start_dt),
            "utc-start-time": self.start_udt,
            "utc-offset": self.start_dt.utcoffset().total_seconds(),
            "log-files": self.logfiles,
            "lag": self.csns
        }
        if self.args.anonymous:
            obj['log-files'] = list(range(len(self.logfiles)))
        json.dump(obj, self.fd, indent=4)


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog='dslogs',
        description='Access log parser that computes replication lags',
        epilog=USAGE
    )
    parser.add_argument('logfiles', nargs="+")  # positional argument
    parser.add_argument('-a', '--anonymous', action='store_true', help="Hide the log file names")
    parser.add_argument('-o', '--output', help="Output file")
    return parser.parse_args()


def main():
    args = parse_arguments()

    fd = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        log_parser = ReplLag(args, fd)
        log_parser.parse_files()
        log_parser.jsonprint()
    finally:
        if args.output:
            fd.close()


if __name__ == "__main__":
    main()

