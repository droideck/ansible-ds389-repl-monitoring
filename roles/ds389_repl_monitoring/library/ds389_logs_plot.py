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
            - UTC offset in timezone format ±HHMM (e.g., "+0000", "+0100", "-0500").
            - Used for timezone adjustment when processing log timestamps.
        required: false
        type: str
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
    suffixes:
        description:
            - List of suffixes to filter the log data.
        required: false
        type: list
        elements: str
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
    utc_offset: "-0100"
    repl_lag_threshold: 5
    start_time: "2024-01-01 00:00:00"
    end_time: "2024-01-02 00:00:00"
    suffixes: ["dc=example,dc=com"]

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
import re
import logging
import tempfile
import sys
import ldap
from datetime import datetime, timezone, tzinfo, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union, NamedTuple

# For plotly visualization
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

# For PNG output with matplotlib
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

from collections import defaultdict
import csv
from collections.abc import Generator


# Define fallback classes for when lib389 is not available
class ChartDataFallback(NamedTuple):
    """Container for chart data series."""
    times: List[datetime]
    lags: List[float]
    durations: List[float]
    hover: List[str]


class VisualizationHelperFallback:
    """Helper class for visualization-related functionality."""

    @staticmethod
    def generate_color_palette(num_colors: int) -> List[str]:
        """Generate a visually pleasing color palette.

        :param num_colors: Number of colors needed
        :returns: List of rgba color strings
        """
        colors = []
        for i in range(num_colors):
            hue = i / num_colors
            saturation = 0.7
            value = 0.9

            # Convert HSV to RGB
            c = value * saturation
            x = c * (1 - abs((hue * 6) % 2 - 1))
            m = value - c

            h_sector = int(hue * 6)
            if h_sector == 0:
                r, g, b = c, x, 0
            elif h_sector == 1:
                r, g, b = x, c, 0
            elif h_sector == 2:
                r, g, b = 0, c, x
            elif h_sector == 3:
                r, g, b = 0, x, c
            elif h_sector == 4:
                r, g, b = x, 0, c
            else:
                r, g, b = c, 0, x

            # Convert to RGB values
            rgb = [int((val + m) * 255) for val in (r, g, b)]
            colors.append(f'rgba({rgb[0]},{rgb[1]},{rgb[2]},0.8)')

        return colors

    @staticmethod
    def prepare_chart_data(csns: Dict[str, Dict[Union[int, str], Dict[str, Any]]]) -> Dict[Tuple[str, str], ChartDataFallback]:
        """Prepare data for visualization."""
        chart_data = defaultdict(lambda: {
            'times': [], 'lags': [], 'durations': [], 'hover': []
        })

        for csn, server_map in csns.items():
            # Gather only valid records (dict, not '__hop_lags__', must have 'logtime')
            valid_records = [
                rec for key, rec in server_map.items()
                if isinstance(rec, dict)
                   and key != '__hop_lags__'
                   and 'logtime' in rec
            ]
            if not valid_records:
                continue

            # Compute global lag for this CSN (earliest vs. latest among valid records)
            t_list = [rec['logtime'] for rec in valid_records]
            earliest = min(t_list)
            latest = max(t_list)
            lag_val = latest - earliest

            # Populate chart data for each server record
            for rec in valid_records:
                suffix_val = rec.get('suffix', 'unknown')
                server_val = rec.get('server_name', 'unknown')

                # Convert numeric UTC to a datetime
                ts_dt = datetime.fromtimestamp(rec['logtime'])

                # Operation duration, defaulting to 0.0 if missing
                duration_val = float(rec.get('duration', 0.0))

                # Build the ChartData slot
                data_slot = chart_data[(suffix_val, server_val)]
                data_slot['times'].append(ts_dt)
                data_slot['lags'].append(lag_val)  # The same global-lag for all servers
                data_slot['durations'].append(duration_val)
                data_slot['hover'].append(
                    f"CSN: {csn}<br>"
                    f"Server: {server_val}<br>"
                    f"Suffix: {suffix_val}<br>"
                    f"Target DN: {rec.get('target_dn', '')}<br>"
                    f"Lag Time: {lag_val:.3f}s<br>"
                    f"Duration: {duration_val:.3f}s"
                )

        # Convert the dict-of-lists into your namedtuple-based ChartData
        return {
            key: ChartDataFallback(
                times=value['times'],
                lags=value['lags'],
                durations=value['durations'],
                hover=value['hover']
            )
            for key, value in chart_data.items()
        }


class ReplicationLogAnalyzerFallback:
    """This class handles:
    - Collecting log files from multiple directories.
    - Parsing them for replication events (CSN).
    - Filtering by suffix.
    - Storing earliest and latest timestamps for each CSN to compute lag.
    - Generating final dictionaries to be used for CSV, HTML, or JSON reporting.
    """

    def __init__(self, log_dirs: List[str], suffixes: Optional[List[str]] = None,
                anonymous: bool = False, only_fully_replicated: bool = False,
                only_not_replicated: bool = False, lag_time_lowest: Optional[float] = None,
                etime_lowest: Optional[float] = None, repl_lag_threshold: Optional[float] = None,
                utc_offset: Optional[str] = None, time_range: Optional[Dict[str, datetime]] = None):
        if not log_dirs:
            raise ValueError("No log directories provided for analysis.")

        self.log_dirs = log_dirs
        self.suffixes = suffixes or []
        self.anonymous = anonymous
        self.only_fully_replicated = only_fully_replicated
        self.only_not_replicated = only_not_replicated
        self.lag_time_lowest = lag_time_lowest
        self.etime_lowest = etime_lowest
        self.repl_lag_threshold = repl_lag_threshold

        # Set timezone
        if utc_offset is not None:
            try:
                self.tz = self._parse_timezone_offset(utc_offset)
            except ValueError as e:
                raise ValueError(f"Invalid UTC offset: {e}")
        else:
            self.tz = timezone.utc

        self.time_range = time_range or {}
        self.csns: Dict[str, Dict[Union[int, str], Dict[str, Any]]] = {}

        # Track earliest global timestamp
        self.start_dt: Optional[datetime] = None
        self.start_udt: Optional[float] = None
        self._logger = logging.getLogger(__name__)

    def _should_include_record(self, csn: str, server_map: Dict[Union[int, str], Dict[str, Any]]) -> bool:
        """Determine if a record should be included based on filtering criteria."""
        if self.only_fully_replicated and len(server_map) != len(self.log_dirs):
            return False
        if self.only_not_replicated and len(server_map) == len(self.log_dirs):
            return False

        # Check lag time threshold
        if self.lag_time_lowest is not None:
            # Only consider dict items, skipping the '__hop_lags__' entry
            t_list = [
                d['logtime']
                for key, d in server_map.items()
                if isinstance(d, dict) and key != '__hop_lags__'
            ]
            if not t_list:
                return False
            lag_time = max(t_list) - min(t_list)
            if lag_time <= self.lag_time_lowest:
                return False

        # Check etime threshold
        if self.etime_lowest is not None:
            for key, record in server_map.items():
                if not isinstance(record, dict) or key == '__hop_lags__':
                    continue
                if float(record.get('etime', 0)) <= self.etime_lowest:
                    return False

        return True

    def _collect_logs(self) -> List[Tuple[str, List[str]]]:
        """For each directory in self.log_dirs, return a tuple (server_name, [logfiles])."""
        data = []
        for dpath in self.log_dirs:
            if not os.path.isdir(dpath):
                self._logger.warning(f"{dpath} is not a directory or not accessible.")
                continue

            server_name = os.path.basename(dpath.rstrip('/'))
            logfiles = []
            for fname in os.listdir(dpath):
                if fname.startswith('access'):  # Only parse access logs
                    full_path = os.path.join(dpath, fname)
                    if os.path.isfile(full_path) and os.access(full_path, os.R_OK):
                        logfiles.append(full_path)
                    else:
                        self._logger.warning(f"Cannot read file: {full_path}")

            logfiles.sort()
            if logfiles:
                data.append((server_name, logfiles))
            else:
                self._logger.warning(f"No accessible 'access' logs found in {dpath}")
        return data

    @staticmethod
    def _parse_timezone_offset(offset_str: str) -> timezone:
        """Parse timezone offset string in ±HHMM format."""
        if not isinstance(offset_str, str):
            raise ValueError("Timezone offset must be a string in ±HHMM format")

        match = re.match(r'^([+-])(\d{2})(\d{2})$', offset_str)
        if not match:
            raise ValueError("Invalid timezone offset format. Use ±HHMM (e.g., -0400, +0530)")

        sign, hours, minutes = match.groups()
        hours = int(hours)
        minutes = int(minutes)

        if hours > 12 or minutes >= 60:
            raise ValueError("Invalid timezone offset. Hours must be ≤12, minutes <60")

        total_minutes = hours * 60 + minutes
        if sign == '-':
            total_minutes = -total_minutes

        return timezone(timedelta(minutes=total_minutes))

    def _compute_hop_lags(self, server_map: Dict[Union[int, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute per-hop replication lags for one CSN across multiple servers."""
        arrivals = []
        for key, data in server_map.items():
            # Skip the special '__hop_lags__' and any non-dict
            if not isinstance(data, dict) or key == '__hop_lags__':
                continue
            arrivals.append({
                'server_name': data.get('server_name', 'unknown'),
                'logtime': data.get('logtime', 0.0),  # numeric UTC timestamp
                'suffix': data.get('suffix'),
                'target_dn': data.get('target_dn'),
            })

        # Sort by ascending logtime
        arrivals.sort(key=lambda x: x['logtime'])

        # Iterate pairs (supplier -> consumer)
        hops = []
        for i in range(1, len(arrivals)):
            supplier = arrivals[i - 1]
            consumer = arrivals[i]
            hop_lag = consumer['logtime'] - supplier['logtime']  # in seconds
            hops.append({
                'supplier': supplier['server_name'],
                'consumer': consumer['server_name'],
                'hop_lag': hop_lag,
                'arrival_consumer': consumer['logtime'],
                'suffix': consumer.get('suffix'),
                'target_dn': consumer.get('target_dn'),
            })

        return hops

    def parse_logs(self) -> None:
        """Parse logs from all directories. Each directory is treated as one server
        unless anonymized, in which case we use 'server_{index}'.
        """
        server_data = self._collect_logs()
        if not server_data:
            raise ValueError("No valid log directories with accessible logs found.")

        for idx, (server_name, logfiles) in enumerate(server_data):
            displayed_name = f"server_{idx}" if self.anonymous else server_name

            # For each log file, parse line by line
            for logfile in logfiles:
                parser = DSLogParser(
                    logname=logfile,
                    suffixes=self.suffixes,
                    tz=self.tz,
                    start_time=self.time_range.get('start'),
                    end_time=self.time_range.get('end')
                )

                for record in parser.parse_file():
                    # If there's no CSN or no suffix, skip
                    if not record.get('csn') or not record.get('suffix'):
                        continue

                    csn = record['csn']
                    ts = record['timestamp']
                    # Convert timestamp to numeric UTC
                    udt = ts.astimezone(timezone.utc).timestamp()

                    # Track earliest global timestamp
                    if self.start_udt is None or udt < self.start_udt:
                        self.start_udt = udt
                        self.start_dt = ts

                    if csn not in self.csns:
                        self.csns[csn] = {}

                    # Build record for this server
                    self.csns[csn][idx] = {
                        'logtime': udt,
                        'etime': record.get('etime'),
                        'server_name': displayed_name,
                        'suffix': record.get('suffix'),
                        'target_dn': record.get('target_dn'),
                        'duration': record.get('duration', 0.0),
                    }

        # Apply filters after collecting all data
        filtered_csns = {}
        for csn, server_map in self.csns.items():
            if self._should_include_record(csn, server_map):
                filtered_csns[csn] = server_map
                # Compute hop-lags and store
                hop_list = self._compute_hop_lags(server_map)
                filtered_csns[csn]['__hop_lags__'] = hop_list

        self.csns = filtered_csns

    def build_result(self) -> Dict[str, Any]:
        """Build the final dictionary object with earliest timestamp, UTC offset, and replication data."""
        if not self.start_dt:
            raise ValueError("No valid replication data collected.")

        obj = {
            "start-time": str(self.start_dt),
            "utc-start-time": self.start_udt,
            "utc-offset": self.start_dt.utcoffset().total_seconds() if self.start_dt.utcoffset() else 0,
            "lag": self.csns
        }
        # Also record the log-files (anonymous or not)
        if self.anonymous:
            obj['log-files'] = list(range(len(self.log_dirs)))
        else:
            obj['log-files'] = self.log_dirs
        return obj

    def generate_report(self, output_dir: str,
                       formats: List[str],
                       report_name: str = "replication_analysis") -> Dict[str, str]:
        """Generate reports in specified formats."""
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except OSError as e:
                raise OSError(f"Could not create directory {output_dir}: {e}")

        if not self.csns:
            raise ValueError("No CSN data available for reporting. Did you call parse_logs()?")

        results = self.build_result()
        generated_files = {}

        # Always produce JSON summary
        summary_path = os.path.join(output_dir, f"{report_name}_summary.json")
        self._generate_summary_json(results, summary_path)
        generated_files["summary"] = summary_path

        # Generate PatternFly format JSON data if requested
        if 'json' in formats:
            json_path = os.path.join(output_dir, f"{report_name}.json")
            self._generate_patternfly_json(results, json_path)
            generated_files["json"] = json_path

        # Generate requested formats
        for fmt in formats:
            fmt = fmt.lower()
            if fmt == 'json':  # Already handled above
                continue

            outfile = os.path.join(output_dir, f"{report_name}.{fmt}")

            if fmt == 'csv':
                self._generate_csv(results, outfile)
                generated_files["csv"] = outfile

            elif fmt == 'html':
                if not PLOTLY_AVAILABLE:
                    self._logger.warning("Plotly not installed. Skipping HTML report.")
                    continue
                fig = self._create_plotly_figure(results)
                self._generate_html(fig, outfile)
                generated_files["html"] = outfile

            elif fmt == 'png':
                if not MATPLOTLIB_AVAILABLE:
                    self._logger.warning("Matplotlib not installed. Skipping PNG report.")
                    continue
                fig = self._create_plotly_figure(results)
                self._generate_png(fig, outfile)
                generated_files["png"] = outfile

            else:
                self._logger.warning(f"Unknown report format requested: {fmt}")

        return generated_files

    def _create_plotly_figure(self, results: Dict[str, Any]):
        """Create a plotly figure for visualization."""
        if not PLOTLY_AVAILABLE:
            raise ImportError("Plotly is required for figure creation")

        # Create figure with 3 subplots: we still generate all 3 for HTML usage
        fig = make_subplots(
            rows=3, cols=1,
            subplot_titles=(
                "Global Replication Lag Over Time",
                "Operation Duration Over Time",
                "Per-Hop Replication Lags"
            ),
            vertical_spacing=0.10,   # spacing between subplots
            shared_xaxes=True
        )

        # Collect all (suffix, server_name) pairs to color consistently
        server_suffix_pairs = set()
        for csn, server_map in self.csns.items():
            for key, rec in server_map.items():
                if not isinstance(rec, dict) or key == '__hop_lags__':
                    continue

                suffix_val = rec.get('suffix', 'unknown')
                srv_val = rec.get('server_name', 'unknown')
                server_suffix_pairs.add((suffix_val, srv_val))

        # Generate colors
        colors = VisualizationHelperFallback.generate_color_palette(len(server_suffix_pairs))

        # Prepare chart data for the first two subplots
        chart_data = VisualizationHelperFallback.prepare_chart_data(self.csns)

        # Plot Per-Hop Lags in row=3 (for HTML usage)
        for csn, server_map in self.csns.items():
            hop_list = server_map.get('__hop_lags__', [])
            for hop in hop_list:
                consumer_ts = hop.get("arrival_consumer", 0.0)
                consumer_dt = datetime.fromtimestamp(consumer_ts)
                hop_lag = hop.get("hop_lag", 0.0)

                hover_text = (
                    f"Supplier: {hop.get('supplier','unknown')}<br>"
                    f"Consumer: {hop.get('consumer','unknown')}<br>"
                    f"Hop Lag: {hop_lag:.3f}s<br>"
                    f"Arrival Time: {consumer_dt}"
                )

                # showlegend=False means these hop-lag traces won't crowd the legend
                fig.add_trace(
                    go.Scatter(
                        x=[consumer_dt],
                        y=[hop_lag],
                        mode='markers',
                        marker=dict(size=7, symbol='circle'),
                        name=f"{hop.get('supplier','?')}→{hop.get('consumer','?')}",
                        text=[hover_text],
                        hoverinfo='text+x+y',
                        showlegend=False
                    ),
                    row=3, col=1
                )

        # Plot Global Lag (row=1) and Durations (row=2)
        for idx, ((sfx, srv), data) in enumerate(sorted(chart_data.items())):
            color = colors[idx % len(colors)]

            # Row=1: Global Replication Lag
            fig.add_trace(
                go.Scatter(
                    x=data.times,
                    y=data.lags,
                    mode='lines+markers',
                    name=f"{sfx} - {srv}",
                    text=data.hover,
                    hoverinfo='text+x+y',
                    line=dict(color=color, width=2),
                    marker=dict(size=6),
                    showlegend=True
                ),
                row=1, col=1
            )

            # Row=2: Operation Durations
            fig.add_trace(
                go.Scatter(
                    x=data.times,
                    y=data.durations,
                    mode='lines+markers',
                    name=f"{sfx} - {srv}",
                    text=data.hover,
                    hoverinfo='text+x+y',
                    line=dict(color=color, width=2, dash='solid'),
                    marker=dict(size=6),
                    showlegend=False
                ),
                row=2, col=1
            )

        # Add a horizontal threshold line to the Replication Lag subplot
        if self.repl_lag_threshold is not None:
            fig.add_hline(
                y=self.repl_lag_threshold,
                line=dict(color='red', width=2, dash='dash'),
                annotation=dict(
                    text=f"Lag Threshold = {self.repl_lag_threshold}s",
                    font=dict(color='red'),
                    showarrow=False,
                    x=1,
                    xanchor='left',
                    y=self.repl_lag_threshold
                ),
                row=1, col=1
            )

        # Figure layout settings
        fig.update_layout(
            title={
                'text': 'Replication Analysis Report',
                'y': 0.96,
                'x': 0.5,
                'xanchor': 'center',
                'yanchor': 'top'
            },
            template='plotly_white',
            hovermode='closest',
            showlegend=True,
            legend=dict(
                title="Suffix / Server",
                yanchor="top",
                y=0.99,
                xanchor="right",
                x=1.15,
                bgcolor='rgba(255, 255, 255, 0.8)'
            ),
            height=900,
            margin=dict(t=100, r=200, l=80)
        )

        # X-axis styling
        fig.update_xaxes(title_text="Time", gridcolor='lightgray', row=1, col=1)
        fig.update_xaxes(
            title_text="Time",
            gridcolor='lightgray',
            rangeslider_visible=True,
            rangeselector=dict(
                buttons=list([
                    dict(count=1, label="1h", step="hour", stepmode="backward"),
                    dict(count=6, label="6h", step="hour", stepmode="backward"),
                    dict(count=1, label="1d", step="day", stepmode="backward"),
                    dict(count=7, label="1w", step="day", stepmode="backward"),
                    dict(step="all")
                ]),
                bgcolor='rgba(255, 255, 255, 0.8)'
            ),
            row=2, col=1
        )
        fig.update_xaxes(title_text="Time", gridcolor='lightgray', row=3, col=1)

        # Y-axis styling
        fig.update_yaxes(title_text="Lag Time (seconds)", gridcolor='lightgray', row=1, col=1)
        fig.update_yaxes(title_text="Duration (seconds)", gridcolor='lightgray', row=2, col=1)
        fig.update_yaxes(title_text="Hop Lag (seconds)", gridcolor='lightgray', row=3, col=1)

        return fig

    def _generate_png(self, fig, outfile: str) -> None:
        """Generate PNG snapshot of the plotly figure using matplotlib.
        For PNG, we deliberately omit the hop-lag (3rd subplot) data.
        """
        try:
            # Create a matplotlib figure with 2 subplots
            plt.figure(figsize=(12, 8))

            # Extract data from the Plotly figure.
            # We'll plot only the first two subplots (y-axis = 'y' or 'y2').
            for trace in fig.data:
                # Check which y-axis the trace belongs to.
                # 'y'  => subplot row=1
                # 'y2' => subplot row=2
                # 'y3' => subplot row=3 (hop-lags) - skip those
                if trace.yaxis == 'y':  # Global Lag subplot
                    plt.subplot(2, 1, 1)
                    plt.plot(trace.x, trace.y, label=trace.name)
                elif trace.yaxis == 'y2':  # Duration subplot
                    plt.subplot(2, 1, 2)
                    plt.plot(trace.x, trace.y, label=trace.name)
                else:
                    # This is likely the hop-lag data on subplot row=3, so skip it
                    continue

            # Format each subplot
            for idx, title in enumerate(['Replication Lag Times', 'Operation Durations']):
                plt.subplot(2, 1, idx + 1)
                plt.title(title)
                plt.xlabel('Time')
                plt.ylabel('Seconds')
                plt.grid(True)
                plt.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
                # Format x-axis as date/time
                plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
                plt.gcf().autofmt_xdate()

            plt.tight_layout()

            # Save with explicit format to ensure proper PNG generation
            plt.savefig(outfile, dpi=300, bbox_inches='tight', format='png')
            plt.close()

            # Verify file was created and is a valid PNG
            if not os.path.exists(outfile) or os.path.getsize(outfile) < 100:
                raise IOError(f"PNG file was not created correctly: {outfile}")

            # Attempt to open and verify the file is a valid PNG
            try:
                with open(outfile, 'rb') as f:
                    header = f.read(8)
                    # Check for PNG signature
                    if header != b'\x89PNG\r\n\x1a\n':
                        raise IOError(f"Generated file does not have a valid PNG signature")
            except Exception as e:
                raise IOError(f"Failed to verify PNG file: {e}")

        except Exception as e:
            # If PNG generation fails, try a direct plotly export as fallback
            try:
                # Try using plotly's built-in image export
                pio.write_image(fig, outfile, format='png', width=1200, height=800, scale=2)

                # Verify the file exists
                if not os.path.exists(outfile):
                    raise IOError("Fallback PNG generation failed")
            except Exception as fallback_err:
                raise IOError(f"Failed to generate PNG report: {e}. Fallback also failed: {fallback_err}")

    def _generate_html(self, fig, outfile: str) -> None:
        """Generate HTML report from the plotly figure."""
        try:
            pio.write_html(
                fig,
                outfile,
                include_plotlyjs='cdn',
                full_html=True,
                include_mathjax='cdn',
                config={
                    'responsive': True,
                    'scrollZoom': True,
                    'modeBarButtonsToAdd': ['drawline', 'drawopenpath', 'eraseshape'],
                    'toImageButtonOptions': {
                        'format': 'png',
                        'filename': 'replication_analysis',
                        'height': 1000,
                        'width': 1500,
                        'scale': 2
                    }
                }
            )
        except Exception as e:
            raise IOError(f"Failed to write HTML report: {e}")

    def _generate_csv(self, results: Dict[str, Any], outfile: str) -> None:
        """Generate a CSV report listing each replication event and its hop-lags."""
        try:
            with open(outfile, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)

                # Global-lag rows
                writer.writerow([
                    'Timestamp', 'Server', 'CSN', 'Suffix', 'Target DN',
                    'Global Lag (s)', 'Duration (s)', 'Operation Etime'
                ])
                for csn, server_map in self.csns.items():
                    # Compute global-lag for normal dict entries
                    t_list = [
                        d['logtime']
                        for key, d in server_map.items()
                        if isinstance(d, dict) and key != '__hop_lags__'
                    ]
                    if not t_list:
                        continue
                    earliest = min(t_list)
                    latest = max(t_list)
                    global_lag = latest - earliest

                    # Write lines for each normal server record
                    for key, data_map in server_map.items():
                        if not isinstance(data_map, dict) or key == '__hop_lags__':
                            continue
                        ts_str = datetime.fromtimestamp(data_map['logtime']).strftime('%Y-%m-%d %H:%M:%S')
                        writer.writerow([
                            ts_str,
                            data_map['server_name'],
                            csn,
                            data_map.get('suffix', 'unknown'),
                            data_map.get('target_dn', ''),
                            f"{global_lag:.3f}",
                            f"{float(data_map.get('duration', 0.0)):.3f}",
                            data_map.get('etime', 'N/A')
                        ])

                # Hop-lag rows
                writer.writerow([])  # blank line
                writer.writerow(["-- Hop-Lag Data --"])
                writer.writerow([
                    'CSN', 'Supplier', 'Consumer', 'Hop Lag (s)', 'Arrival (Consumer)', 'Suffix', 'Target DN'
                ])
                for csn, server_map in self.csns.items():
                    hop_list = server_map.get('__hop_lags__', [])
                    for hop_info in hop_list:
                        hop_lag_str = f"{hop_info['hop_lag']:.3f}"
                        arrival_ts = datetime.fromtimestamp(hop_info['arrival_consumer']).strftime('%Y-%m-%d %H:%M:%S')
                        writer.writerow([
                            csn,
                            hop_info['supplier'],
                            hop_info['consumer'],
                            hop_lag_str,
                            arrival_ts,
                            hop_info.get('suffix', 'unknown'),
                            hop_info.get('target_dn', '')
                        ])

        except Exception as e:
            raise IOError(f"Failed to write CSV report {outfile}: {e}")

    def _generate_summary_json(self, results: Dict[str, Any], outfile: str) -> None:
        """Create a JSON summary from the final dictionary."""
        global_lag_times = []
        hop_lag_times = []
        suffix_updates = {}

        for csn, server_map in self.csns.items():
            t_list = [
                rec['logtime']
                for key, rec in server_map.items()
                if isinstance(rec, dict) and key != '__hop_lags__' and 'logtime' in rec
            ]
            if not t_list:
                continue

            # Global earliest vs. latest (for "global lag")
            earliest = min(t_list)
            latest = max(t_list)
            global_lag = latest - earliest
            global_lag_times.append(global_lag)

            # Suffix counts
            for key, record in server_map.items():
                # Only process normal server records, skip the special '__hop_lags__'
                if not isinstance(record, dict) or key == '__hop_lags__':
                    continue

                sfx = record.get('suffix', 'unknown')
                suffix_updates[sfx] = suffix_updates.get(sfx, 0) + 1

            # Hop-lag data
            hop_list = server_map.get('__hop_lags__', [])
            for hop_info in hop_list:
                hop_lag_times.append(hop_info['hop_lag'])

        # Compute global-lag stats
        if global_lag_times:
            min_lag = min(global_lag_times)
            max_lag = max(global_lag_times)
            avg_lag = sum(global_lag_times) / len(global_lag_times)
        else:
            min_lag = 0.0
            max_lag = 0.0
            avg_lag = 0.0

        # Compute hop-lag stats
        if hop_lag_times:
            min_hop_lag = min(hop_lag_times)
            max_hop_lag = max(hop_lag_times)
            avg_hop_lag = sum(hop_lag_times) / len(hop_lag_times)
            total_hops = len(hop_lag_times)
        else:
            min_hop_lag = 0.0
            max_hop_lag = 0.0
            avg_hop_lag = 0.0
            total_hops = 0

        # Build analysis summary
        analysis_summary = {
            'total_servers': len(self.log_dirs),
            'analyzed_logs': len(self.csns),
            'total_updates': sum(suffix_updates.values()),
            'minimum_lag': min_lag,
            'maximum_lag': max_lag,
            'average_lag': avg_lag,
            'minimum_hop_lag': min_hop_lag,
            'maximum_hop_lag': max_hop_lag,
            'average_hop_lag': avg_hop_lag,
            'total_hops': total_hops,
            'updates_by_suffix': suffix_updates,
            'time_range': {
                'start': results['start-time'],
                'end': 'current'
            }
        }

        # Wrap it up for writing
        summary = {
            'analysis_summary': analysis_summary
        }

        # Write to JSON
        try:
            with open(outfile, 'w') as f:
                json.dump(summary, f, indent=4, default=str)
        except Exception as e:
            raise IOError(f"Failed to write JSON summary to {outfile}: {e}")

    def _generate_patternfly_json(self, results: Dict[str, Any], outfile: str) -> None:
        """Generate JSON specifically formatted for PatternFly 5 charts."""
        # Prepare chart data using the visualization helper
        chart_data = VisualizationHelperFallback.prepare_chart_data(self.csns)

        # Create a structure for PatternFly 5 scatter-line chart
        series_data = []

        # Create a color palette for consistent coloring
        all_keys = list(chart_data.keys())
        color_palette = VisualizationHelperFallback.generate_color_palette(len(all_keys))

        for idx, ((suffix, server_name), data) in enumerate(chart_data.items()):
            # Skip if no data points
            if not data.times:
                continue

            # Sort data by time for better chart rendering
            sorted_indices = sorted(range(len(data.times)), key=lambda i: data.times[i])

            # Prepare data points
            datapoints = []
            for i in sorted_indices:
                # Format time as ISO string for consistent serialization
                timestamp = data.times[i].isoformat()
                lag_value = data.lags[i]
                duration = data.durations[i]
                hover_text = data.hover[i]

                datapoints.append({
                    "name": server_name,
                    "x": timestamp,  # ISO format timestamp
                    "y": lag_value,  # Lag value for y-axis
                    "duration": duration,  # Additional data
                    "hoverInfo": hover_text  # Hover text
                })

            # Add to series
            series_data.append({
                "datapoints": datapoints,
                "legendItem": {
                    "name": f"{server_name} ({suffix})"
                },
                "color": color_palette[idx % len(color_palette)]
            })

        # Create series for hop lags if available
        hop_series = []
        hop_data = {}

        # Process hop lag data
        for csn, server_map in self.csns.items():
            hop_list = server_map.get('__hop_lags__', [])

            for hop_info in hop_list:
                # Create a key based on source and target
                source = hop_info.get('supplier', 'unknown')
                target = hop_info.get('consumer', 'unknown')
                key = f"{source} → {target}"

                if key not in hop_data:
                    hop_data[key] = {
                        'times': [],
                        'lags': [],
                        'hover': []
                    }

                # Add data point
                timestamp = datetime.fromtimestamp(hop_info['arrival_consumer'])
                hop_data[key]['times'].append(timestamp)
                hop_data[key]['lags'].append(hop_info['hop_lag'])
                hop_data[key]['hover'].append(
                    f"CSN: {csn}<br>"
                    f"Source: {source}<br>"
                    f"Target: {target}<br>"
                    f"Hop Lag: {hop_info['hop_lag']:.3f}s"
                )

        # Generate color palette for hop data
        hop_color_palette = VisualizationHelperFallback.generate_color_palette(len(hop_data))

        # Create hop series data
        for idx, (key, data) in enumerate(hop_data.items()):
            # Skip if no data points
            if not data['times']:
                continue

            # Sort data by time
            sorted_indices = sorted(range(len(data['times'])), key=lambda i: data['times'][i])

            # Prepare data points
            datapoints = []
            for i in sorted_indices:
                # Format time as ISO string for consistent serialization
                timestamp = data['times'][i].isoformat()
                lag_value = data['lags'][i]
                hover_text = data['hover'][i]

                datapoints.append({
                    "name": key,
                    "x": timestamp,
                    "y": lag_value,
                    "hoverInfo": hover_text
                })

            # Add to hop series
            hop_series.append({
                "datapoints": datapoints,
                "legendItem": {
                    "name": key
                },
                "color": hop_color_palette[idx % len(hop_color_palette)]
            })

        # Final data structure for PatternFly 5 charts
        pf_data = {
            "replicationLags": {
                "title": "Global Replication Lag Over Time",
                "yAxisLabel": "Lag Time (seconds)",
                "xAxisLabel": "Time",
                "series": series_data
            },
            "hopLags": {
                "title": "Per-Hop Replication Lags",
                "yAxisLabel": "Hop Lag Time (seconds)",
                "xAxisLabel": "Time",
                "series": hop_series
            },
            "metadata": {
                "totalServers": len(self.log_dirs),
                "analyzedLogs": len(self.csns),
                "totalUpdates": sum(len([
                    rec for key, rec in server_map.items()
                    if isinstance(rec, dict) and key != '__hop_lags__'
                ]) for csn, server_map in self.csns.items()),
                "timeRange": {
                    "start": results['start-time'],
                    "end": results.get('end-time', 'current')
                }
            }
        }

        # Write to JSON file
        try:
            with open(outfile, 'w') as f:
                json.dump(pf_data, f, indent=4, default=str)
        except Exception as e:
            raise IOError(f"Failed to write PatternFly JSON to {outfile}: {e}")

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


def convert_to_timezone(dt_string, utc_offset="+0000"):
    """Convert date string to timezone-aware datetime"""
    try:
        dt = datetime.strptime(dt_string, "%Y-%m-%d %H:%M:%S")

        # Parse utc_offset from string format (±HHMM) to seconds
        if isinstance(utc_offset, str) and re.match(r'^[+-]\d{4}$', utc_offset):
            sign = -1 if utc_offset[0] == '-' else 1
            hours = int(utc_offset[1:3])
            minutes = int(utc_offset[3:5])
            offset_seconds = sign * (hours * 3600 + minutes * 60)
        else:
            # Fallback to treating as seconds directly
            try:
                offset_seconds = int(utc_offset)
            except (ValueError, TypeError):
                offset_seconds = 0

        tz = timezone(timedelta(seconds=offset_seconds))
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None

# Try to import lib389 first
try:
    from lib389.repltools import ReplicationLogAnalyzer
    LIB389_AVAILABLE = True
except ImportError:
    ReplicationLogAnalyzer = ReplicationLogAnalyzerFallback
    LIB389_AVAILABLE = False
    logging.getLogger(__name__).warning("lib389 is not available. Using fallback implementation.")

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
            utc_offset=dict(type='str', required=False),
            repl_lag_threshold=dict(type='float', required=False),
            start_time=dict(type='str', required=False, default='1970-01-01 00:00:00'),
            end_time=dict(type='str', required=False, default='9999-12-31 23:59:59'),
            suffixes=dict(type='list', elements='str', required=False, default=[])
        ),
        supports_check_mode=True
    )

    if not LIB389_AVAILABLE:
        module.warn("Using fallback implementation as lib389 is not available. This is a temporary solution.")

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
        suffixes = module.params['suffixes']
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
        utc_offset = module.params.get('utc_offset', "+0000")

        if module.params.get('start_time'):
            time_range['start'] = convert_to_timezone(
                module.params['start_time'],
                utc_offset
            )

        if module.params.get('end_time'):
            time_range['end'] = convert_to_timezone(
                module.params['end_time'],
                utc_offset
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
            utc_offset=utc_offset,
            time_range=time_range
        )

        # Instead of parsing the logs, we'll directly set the CSN data
        # This is needed because we already have the parsed data in Ansible format
        analyzer.csns = csns

        if json_data.get('start-time'):
            try:
                analyzer.start_dt = datetime.fromisoformat(json_data['start-time'])
                analyzer.start_udt = json_data.get('utc-start-time', analyzer.start_dt.timestamp())
            except (ValueError, TypeError) as e:
                module.warn(f"Could not parse start-time from input: {e}")
                # Fallback: use the earliest timestamp from the CSN data
                try:
                    earliest_time = float('inf')
                    for csn, server_map in csns.items():
                        for _, record in server_map.items():
                            if isinstance(record, dict) and 'logtime' in record:
                                earliest_time = min(earliest_time, record['logtime'])

                    if earliest_time != float('inf'):
                        analyzer.start_dt = datetime.fromtimestamp(earliest_time)
                        analyzer.start_udt = earliest_time
                    else:
                        analyzer.start_dt = datetime.now()
                        analyzer.start_udt = analyzer.start_dt.timestamp()
                except Exception:
                    analyzer.start_dt = datetime.now()
                    analyzer.start_udt = analyzer.start_dt.timestamp()

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
