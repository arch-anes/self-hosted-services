FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-20.04

RUN apt-get update && apt-get install -y python3-pip && pip3 install ansible==3.4.0 mitogen==0.3.0rc1 boto3

RUN mkdir -p /etc/ansible && touch /etc/ansible/ansible.cfg && \
    echo "[defaults]" > /etc/ansible/ansible.cfg && \
    echo "strategy_plugins = /usr/local/lib/python3.8/dist-packages/ansible_mitogen/plugins/strategy" >> /etc/ansible/ansible.cfg  && \
    echo "strategy = mitogen_linear" >> /etc/ansible/ansible.cfg
