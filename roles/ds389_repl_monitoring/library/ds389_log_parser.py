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
  suffixes:
    description:
      - List of LDAP directory suffixes to monitor for replication.
      - Example: ["dc=example,dc=com"]
    required: false
    type: list
    elements: str
    default: []
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
    suffixes:
      - "dc=example,dc=com"
'''

from ansible.module_utils.basic import AnsibleModule
import json
import logging
import sys
import os
import re
import ldap
from datetime import datetime, timezone, tzinfo
from typing import Dict, List, Optional, Tuple, Generator, Any, Union


def normalizeDN(dn, usespace=False):
    """Normalize a DN string by lowercasing and using consistent spacing.

    Uses ldap.explode_dn for parsing and proper normalization.
    """
    if not dn:
        return ""

    try:
        ary = ldap.explode_dn(dn.lower())
        return (", " if usespace else ",").join(ary)
    except ldap.DECODING_ERROR:
        raise ValueError(f"Unable to normalize DN '{dn}'")


# Fallback implementation when lib389 is not available
class DSLogParserFallback:
    """Base parser for Directory Server logs, focusing on replication events."""

    REGEX_TIMESTAMP = re.compile(
        r'\[(?P<day>\d*)\/(?P<month>\w*)\/(?P<year>\d*):(?P<hour>\d*):(?P<minute>\d*):(?P<second>\d*)(\.(?P<nanosecond>\d*))+\s(?P<tz>[\+\-]\d{2})(?P<tz_minute>\d{2})'
    )
    REGEX_LINE = re.compile(
        r'\s(?P<quoted>[^= ]+="[^"]*")|(?P<var>[^= ]+=[^\s]+)|(?P<keyword>[^\s]+)'
    )
    MONTH_LOOKUP = {
        'Jan': "01", 'Feb': "02", 'Mar': "03", 'Apr': "04",
        'May': "05", 'Jun': "06", 'Jul': "07", 'Aug': "08",
        'Sep': "09", 'Oct': "10", 'Nov': "11", 'Dec': "12"
    }

    class ParserResult:
        """Container for parsed log line results."""
        def __init__(self):
            self.keywords: List[str] = []
            self.vars: Dict[str, str] = {}
            self.raw: Any = None
            self.timestamp: Optional[str] = None
            self.line: Optional[str] = None

    def __init__(self, logname: str, suffixes: List[str],
                tz: tzinfo = timezone.utc,
                start_time: Optional[datetime] = None,
                end_time: Optional[datetime] = None,
                batch_size: int = 1000):
        """Initialize the parser with time range filtering.

        :param logname: Path to the log file
        :param suffixes: Suffixes that should be tracked
        :param tz: Timezone to interpret log timestamps
        :param start_time: Optional start time filter
        :param end_time: Optional end time filter
        :param batch_size: Batch size for memory-efficient processing
        """
        self.logname = logname
        self.lineno = 0
        self.line: Optional[str] = None
        self.tz = tz
        self._suffixes = self._normalize_suffixes(suffixes)

        # Ensure start_time and end_time are timezone-aware
        self.start_time = self._ensure_timezone_aware(start_time) if start_time else None
        self.end_time = self._ensure_timezone_aware(end_time) if end_time else None

        self.batch_size = batch_size
        self.pending_ops: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._logger = logging.getLogger(__name__)
        self._current_batch: List[Dict[str, Any]] = []

    def _ensure_timezone_aware(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware using configured timezone."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    @staticmethod
    def parse_timestamp(ts: Union[str, datetime]) -> datetime:
        """Parse a timestamp into a datetime object."""
        if isinstance(ts, datetime):
            return ts

        match = DSLogParser.REGEX_TIMESTAMP.match(ts)
        if not match:
            raise ValueError(f"Invalid timestamp format: {ts}")

        parsed = match.groupdict()
        iso_ts = '{YEAR}-{MONTH}-{DAY}T{HOUR}:{MINUTE}:{SECOND}{TZH}:{TZM}'.format(
            YEAR=parsed['year'],
            MONTH=DSLogParser.MONTH_LOOKUP[parsed['month']],
            DAY=parsed['day'],
            HOUR=parsed['hour'],
            MINUTE=parsed['minute'],
            SECOND=parsed['second'],
            TZH=parsed['tz'],
            TZM=parsed['tz_minute']
        )

        # Create timezone-aware datetime
        dt = datetime.fromisoformat(iso_ts)

        # Handle nanoseconds if present
        if parsed['nanosecond']:
            dt = dt.replace(microsecond=int(parsed['nanosecond']) // 1000)

        return dt

    def _is_in_time_range(self, timestamp: datetime) -> bool:
        """Check if timestamp is within configured time range."""
        # Ensure timestamp is timezone-aware and in the same timezone
        aware_timestamp = self._ensure_timezone_aware(timestamp)

        if self.start_time and aware_timestamp < self.start_time:
            return False
        if self.end_time and aware_timestamp > self.end_time:
            return False
        return True

    def _cleanup_resources(self):
        """Clean up any remaining resources."""
        self.pending_ops.clear()
        self._current_batch.clear()

    def _process_operation(self, result: 'DSLogParser.ParserResult') -> Optional[Dict[str, Any]]:
        """Process operation with memory optimization."""
        conn = result.vars.get('conn')
        op = result.vars.get('op')

        if not conn or not op:
            return None

        conn_op = (conn, op)

        # Handle completion keywords
        if any(kw in result.keywords for kw in ['RESULT', 'ABANDON', 'DISCONNECT']):
            if conn_op in self.pending_ops:
                op_data = self.pending_ops.pop(conn_op)
                return self._create_record(result, op_data)
            return None

        # Manage pending operations
        if conn_op not in self.pending_ops:
            self.pending_ops[conn_op] = {
                'start_time': result.timestamp,
                'last_time': result.timestamp,
                'conn': conn,
                'op': op,
                'suffix': None,
                'target_dn': None
            }
        else:
            # Update last seen time
            self.pending_ops[conn_op]['last_time'] = result.timestamp

        # Check for DN and suffix
        if 'dn' in result.vars:
            matched_suffix = self._match_suffix(result.vars['dn'])
            if matched_suffix:
                self.pending_ops[conn_op]['suffix'] = matched_suffix
                self.pending_ops[conn_op]['target_dn'] = result.vars['dn']

        # Check for CSN
        if 'csn' in result.vars:
            self.pending_ops[conn_op]['csn'] = result.vars['csn']

        return None

    def parse_file(self) -> Generator[Dict[str, Any], None, None]:
        """Parse log file with memory-efficient batch processing."""
        try:
            with open(self.logname, 'r', encoding='utf-8') as f:
                for self.line in f:
                    self.lineno += 1
                    try:
                        result = self.parse_line()
                        if result:
                            # Record is returned if operation is complete
                            record = self._process_operation(result)
                            if record:
                                self._current_batch.append(record)

                                # Yield batch if full
                                if len(self._current_batch) >= self.batch_size:
                                    yield from self._process_batch()

                    except Exception as e:
                        self._logger.warning(
                            f"Error parsing line {self.lineno} in {self.logname}: {e}"
                        )
                        continue

                # Process any remaining operations in the final batch
                if self._current_batch:
                    yield from self._process_batch()

                # Handle any remaining pending operations
                yield from self._process_remaining_ops()

        except (OSError, IOError) as e:
            raise IOError(f"Failed to open or read log file {self.logname}: {e}")
        finally:
            self._cleanup_resources()

    def parse_line(self) -> Optional['DSLogParser.ParserResult']:
        """Parse a single line, returning a ParserResult object if recognized."""
        line = self.line
        if not line:
            return None

        # Extract timestamp
        timestamp_match = self.REGEX_TIMESTAMP.match(line)
        if not timestamp_match:
            return None

        result = DSLogParser.ParserResult()
        result.raw = line
        result.timestamp = timestamp_match.group(0)

        # Remove the timestamp portion from the line for parsing
        after_ts = line[timestamp_match.end():].strip()
        # Use REGEX_LINE to parse remaining content
        for match in self.REGEX_LINE.finditer(after_ts):
            if match.group('keyword'):
                # Something that is not in key=value format
                result.keywords.append(match.group('keyword'))
            elif match.group('var'):
                # key=value
                var = match.group('var')
                k, v = var.split('=', 1)
                result.vars[k] = v.strip()
            elif match.group('quoted'):
                # key="value"
                kv = match.group('quoted')
                k, v = kv.split('=', 1)
                result.vars[k] = v.strip('"')

        return result

    def _normalize_suffixes(self, suffixes: List[str]) -> List[str]:
        """Normalize suffixes for matching (lowercase, remove spaces)."""
        normalized = [normalizeDN(s) for s in suffixes if s]
        # Sort by length descending so we match the longest suffix first
        return sorted(normalized, key=len, reverse=True)

    def _match_suffix(self, dn: str) -> Optional[str]:
        """Return a matched suffix if dn ends with one of our suffixes."""
        if not dn:
            return None
        dn_clean = normalizeDN(dn)
        for sfx in self._suffixes:
            if dn_clean.endswith(sfx):
                return sfx
        return None

    def _process_batch(self) -> Generator[Dict[str, Any], None, None]:
        """Process and yield a batch of operations."""
        for record in self._current_batch:
            try:
                # Handle timestamp regardless of type
                if isinstance(record['timestamp'], str):
                    timestamp = self.parse_timestamp(record['timestamp'])
                else:
                    timestamp = record['timestamp']

                if not self._is_in_time_range(timestamp):
                    continue

                record['timestamp'] = timestamp
                yield record

            except ValueError as e:
                self._logger.warning(
                    f"Error processing timestamp in batch: {e}"
                )
        self._current_batch.clear()

    def _create_record(self, result: Optional['DSLogParser.ParserResult'] = None,
                    op_data: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Create a standardized record from either a parser result or operation data."""
        try:
            # Determine source of data
            if result and op_data:
                # Active operation
                timestamp = self.parse_timestamp(result.timestamp)
                conn = result.vars.get('conn')
                op = result.vars.get('op')
                csn = result.vars.get('csn')
                etime = result.vars.get('etime')
                duration = self._calculate_duration(op_data['start_time'], result.timestamp)
            elif op_data:
                # Remaining operation
                timestamp = op_data.get('last_time', op_data['start_time'])
                if isinstance(timestamp, str):
                    timestamp = self.parse_timestamp(timestamp)
                conn = op_data.get('conn')
                op = op_data.get('op')
                csn = op_data.get('csn')
                etime = None
                duration = self._calculate_duration(
                    op_data['start_time'],
                    timestamp
                )
            else:
                self._logger.warning("Invalid record creation attempt: no data provided")
                return None

            # Validate required fields
            if not all([timestamp, conn, op]):
                self._logger.debug(
                    f"Missing required fields: timestamp={timestamp}, conn={conn}, op={op}"
                )
                return None

            # Create standardized record
            record = {
                'timestamp': timestamp,
                'conn': conn,
                'op': op,
                'csn': csn,
                'suffix': op_data.get('suffix'),
                'target_dn': op_data.get('target_dn'),
                'duration': duration,
                'etime': etime
            }

            # Verify time range
            if not self._is_in_time_range(timestamp):
                return None

            return record

        except Exception as e:
            self._logger.warning(f"Error creating record: {e}")
            return None

    def _process_remaining_ops(self) -> Generator[Dict[str, Any], None, None]:
        """Process any remaining pending operations."""
        for (conn, op), op_data in list(self.pending_ops.items()):
            try:
                if 'csn' in op_data and 'suffix' in op_data:
                    record = self._create_record(op_data=op_data)
                    if record:
                        yield record
            except Exception as e:
                self._logger.warning(
                    f"Error processing remaining operation {conn}-{op}: {e}"
                )
            finally:
                self.pending_ops.pop((conn, op), None)

    def _calculate_duration(self, start: Union[str, datetime],
                        end: Union[str, datetime]) -> float:
        """Compute duration between two timestamps. """
        try:
            if isinstance(start, str):
                st = self.parse_timestamp(start)
            else:
                st = start

            if isinstance(end, str):
                et = self.parse_timestamp(end)
            else:
                et = end

            return (et - st).total_seconds()
        except (ValueError, TypeError):
            return 0.0

# Try to import lib389 first
try:
    from lib389.repltools import DSLogParser
    LIB389_AVAILABLE = True
except ImportError:
    DSLogParser = DSLogParserFallback
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
        self.suffixes = args.get('suffixes', [])

    def parse_with_lib389(self):
        """Parse all log files"""
        for logfile in self.logfiles:
            # Initialize the DSLogParser with the logfile
            # If we have suffixes, use them to filter the logs
            parser = DSLogParser(logname=logfile, suffixes=self.suffixes, batch_size=1000)

            # Process parsed records
            for record in parser.parse_file():
                # Skip records without CSN or suffix
                if not record.get('csn'):
                    continue

                csn = record.get('csn')
                timestamp = record.get('timestamp')

                # Convert to UTC timestamp
                if isinstance(timestamp, datetime):
                    udt = timestamp.astimezone(timezone.utc).timestamp()
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
            "start-time": str(self.start_dt) if self.start_dt else str(datetime.now(timezone.utc)),
            "utc-start-time": self.start_udt if self.start_udt else datetime.now(timezone.utc).timestamp(),
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
            output_file=dict(required=True, type='str'),
            suffixes=dict(required=False, type='list', elements='str', default=[])
        ),
        supports_check_mode=True
    )

    # Check if lib389 is available
    if not LIB389_AVAILABLE:
        module.warn("Using fallback implementation as lib389 is not available. This is a temporary solution.")

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