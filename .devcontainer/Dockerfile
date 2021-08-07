FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-20.04

RUN apt-get update && apt-get install -y python3-pip gettext-base && pip3 install ansible==3.4.0 mitogen==0.3.0rc1 boto3

RUN mkdir -p /etc/ansible
COPY ansible.cfg /etc/ansible/ansible.cfg
