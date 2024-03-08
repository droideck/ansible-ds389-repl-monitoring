# Ansible 389 Director Server Replication Monitoring Project

## Overview

The Ansible 389 DS Replication Monitoring Project is designed to facilitate the monitoring and logging of replication lag on 389 DS agreements across an entire topology. Utilizing CSN and etime values, the project offers capabilities to detect and log instances where replication is lagging behind a pre-defined threshold. The data is gathered and stored in the user-defined directory in CSV and PNG graph formats.

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
- `ds389_repl_monitoring_log_dir`: The directory where replication logs are stored.
- `ds389_repl_monitoring_result_dir`: Directory for storing the output results like CSV and PNG files.
- `ds389_repl_monitoring_lag_threshold`: Threshold in seconds for what constitutes a replication lag.

### Inventory Requirements
This role is designed to work with a dynamic inventory that includes all hosts participating in the 389 DS topology.

### Dependencies
- Python3
- Python-matplotlib (for generating PNG graphs)

### Example Playbook
```yaml
- name: Monitor Replication
  hosts: staging
  become: true
  roles:
    - ../roles/ds389_repl_monitoring
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
