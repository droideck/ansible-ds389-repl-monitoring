# Ansible 389 Director Server Replication Monitoring Project

## Overview

The Ansible Replication Monitoring Project is designed to facilitate the monitoring and logging of replication lag on 389 DS agreements across an entire topology. Utilizing CSN and etime values, the project offers capabilities to detect and log instances where replication is lagging behind a pre-defined threshold. The data is gathered and stored in the user-defined directory in CSV and PNG graph formats.

## Installation

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/your-org/ansible-replication-monitoring.git
   cd ansible-replication-monitoring
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

Execute the `monitor-replication.yml` playbook to start monitoring replication. This playbook includes the Replication-Monitoring role to gather CSN and etime values and check for replication lags. After that, the role stores the monitored data into the user-defined directory.

```bash
ansible-playbook playbooks/monitor-replication.yml -i inventory/your_inventory.yml
```

## Replication-Monitoring Role

### Role Description
The Replication-Monitoring role is an integral part of the Ansible Replication Monitoring Project. It is responsible for gathering CSN and etime values from 389 DS access logs, detecting replication lags based on predefined thresholds, and logging this information for analysis.

### Role Variables
- `replication_monitoring_log_dir`: The directory where replication logs are stored.
- `replication_monitoring_result_dir`: Directory for storing the output results like CSV and PNG files.
- `replication_monitoring_lag_threshold`: Threshold in seconds for what constitutes a replication lag.

### Inventory Requirements
This role is designed to work with a dynamic inventory that includes all hosts participating in the 389 DS topology.

### Dependencies
- Python3
- Python-matplotlib (for generating PNG graphs)

### Example Playbook
```yaml
- name: Monitor Replication
  hosts: all
  become: yes
  roles:
    - ../roles/Replication-Monitoring
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
- The role assumes that access logs are present in the `replication_monitoring_log_dir`.
- The role should be run not more often than once per hour as per current logic.
