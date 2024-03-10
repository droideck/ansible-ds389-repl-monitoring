# Ansible 389 Director Server Replication Monitoring Project

## Overview

The Ansible 389 DS Replication Monitoring Project is designed to facilitate the monitoring and logging of replication lag on 389 DS agreements across an entire topology. Utilizing CSN and etime values, the project offers capabilities to detect and log instances where replication is lagging behind a pre-defined threshold. The data is gathered and stored in the user-defined directory in CSV and PNG graph formats.

## Requirements

- Ansible 2.9 or later
- Python 3.6 or later
- 389 Directory Server instances

## Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/droideck/ansible-389ds-replication-monitoring.git
   cd ansible-389ds-replication-monitoring
   ```

2. **Install Dependencies:**
   Ensure Ansible and necessary packages are installed on your system. Refer to the Ansible documentation for installation guidelines.

## Usage

### Setting Up the Environment

Run the `setup-environment.yml` playbook to prepare your environment. This playbook ensures necessary packages are installed, sets up the log directory, and tests connectivity to all hosts.

```bash
ansible-playbook playbooks/setup-environment.yml -i inventory/your_inventory.yml
```

### Monitoring Replication

Execute the `monitor-replication.yml` playbook to start monitoring replication. This playbook includes the ds389_repl_monitoring role to gather CSN and etime values and check for replication lags. After that, the role stores the monitored data into the user-defined directory.

```bash
ansible-playbook playbooks/monitor-replication.yml -i inventory/your_inventory.yml
```

## ds389_repl_monitoring Role

### Role Description
The ds389_repl_monitoring role is an integral part of the Ansible 389 DS Replication Monitoring Project. It is responsible for gathering CSN and etime values from 389 DS access logs, detecting replication lags based on predefined thresholds, and logging this information for analysis.

### Role Variables

The following variables can be configured for the `ds389_repl_monitoring` role:

#### ds389_repl_monitoring_lag_threshold
- Description: Threshold for replication lag monitoring (in seconds). If the replication lag exceeds this value, it will be considered as a lag event.
- Default value: 10

#### ds389_repl_monitoring_result_dir
- Description: Directory to store replication monitoring results. The generated CSV and PNG files will be saved in this directory.
- Default value: '/tmp'

#### ds389_repl_monitoring_only_fully_replicated
- Description: Filter to show only changes replicated on all replicas. If set to true, only changes that have been replicated to all replicas will be considered.
- Default value: false

#### ds389_repl_monitoring_only_not_replicated
- Description: Filter to show only changes not replicated on all replicas. If set to true, only changes that have not been replicated to all replicas will be considered.
- Default value: false

#### ds389_repl_monitoring_lag_time_lowest
- Description: Filter to show only changes with lag time greater than or equal to the specified value (in seconds). Changes with a lag time lower than this value will be excluded from the monitoring results.
- Default value: 0

#### ds389_repl_monitoring_etime_lowest
- Description: Filter to show only changes with execution time (etime) greater than or equal to the specified value (in seconds). Changes with an execution time lower than this value will be excluded from the monitoring results.
- Default value: 0

#### ds389_repl_monitoring_utc_offset
- Description: UTC offset in seconds for timezone adjustment. This value will be used to adjust the log timestamps to the desired timezone.
- Default value: 0

#### ds389_repl_monitoring_tmp_path
- Description: Temporary directory path for storing intermediate files. This directory will be used to store temporary files generated during the monitoring process.
- Default value: "/tmp"

#### ds389_repl_monitoring_tmp_analysis_output_file_path
- Description: Path to the temporary analysis output file for each host. This file will contain the parsed replication data for each individual host.
- Default value: "{{ ds389_repl_monitoring_tmp_path }}/{{ inventory_hostname }}_analysis_output.json"

#### ds389_repl_monitoring_tmp_merged_output_file_path
- Description: Path to the temporary merged output file. This file will contain the merged replication data from all hosts.
- Default value: "{{ ds389_repl_monitoring_tmp_path }}/merged_output.json"

These variables can be overridden in the playbook or inventory to customize the behavior of the ds389_repl_monitoring role according to your specific requirements.

### Inventory Requirements
This role is designed to work with a dynamic inventory that includes all hosts participating in the 389 DS topology.

### Dependencies
- Python-matplotlib (for generating PNG graphs)

### Example Playbook
```yaml
- name: Monitor Replication
  hosts: staging
  roles:
    - role: ds389_repl_monitoring
      vars:
        ds389_repl_monitoring_lag_threshold: 20
        ds389_repl_monitoring_result_dir: '/var/log/ds389_repl_monitoring'
```

## Testing

The project is configured with Ansible Molecule for testing using Docker. To run tests:

1. Navigate to the root of the project.
2. Execute Molecule tests:
   ```bash
   molecule test
   ```

## Future Plans

- **Notification System:**
  Plan to implement a notification system for critical issues, potentially integrating with platforms like Slack.

- **Continuous Monitoring:**
  Consideration for making `monitor-replication.yml` continuous, allowing users to call the analyze playbook at any time for current state reports or schedule it.

- **Security Enhancements:**
  Implement secure handling of sensitive inventory data using mechanisms like Ansible Vault.

- **Version Control:**
  Manage different 389 DS versions to accommodate variations in log formats.

- **Comprehensive Documentation:**
  Continued development of documentation for setup, configuration, and usage.

## Additional Notes
- Ensure that Python3 and Python-matplotlib are installed on all target hosts for the successful execution of this role.
- The role assumes that access logs are present in the `ds389_repl_monitoring_log_dir`.
- The role should be run not more often than once per hour as per current logic.

Notable mentions: Thank you, Pierre Rogier (@progier389) for dslogs and dslogsplot tools!
