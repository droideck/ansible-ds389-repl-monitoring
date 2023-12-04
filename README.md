# Ansible 389 Director Server Replication Monitoring Project

## Overview

The Ansible Replication Monitoring Project is designed to facilitate the monitoring and logging of replication lag on 389 DS agreements across an entire topology. Utilizing CSN values, the project offers capabilities to detect and log instances where replication is lagging behind a pre-defined threshold. It also provides tools for retrospective analysis to identify lag during past events as it monitors access logs.

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

Execute the `monitor-replication.yml` playbook to start monitoring replication. This playbook includes the Replication-Monitor role to gather CSN values and check for replication lags.

```bash
ansible-playbook playbooks/monitor-replication.yml -i inventory/your_inventory.yml
```

## Testing

The project is configured with Ansible Molecule for testing using Docker. To run tests:

1. Navigate to the root of the project.
2. Execute Molecule tests:
   ```bash
   molecule test
   ```

## Future Plans

- **Enhanced Replication Monitoring Capabilities:**
  Future iterations will expand beyond gathering CSN values to include more sophisticated replication monitoring features.

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
