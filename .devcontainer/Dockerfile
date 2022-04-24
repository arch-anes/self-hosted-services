FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-20.04

RUN sudo curl -fsSLo /usr/share/keyrings/kubernetes-archive-keyring.gpg https://packages.cloud.google.com/apt/doc/apt-key.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/kubernetes-archive-keyring.gpg] https://apt.kubernetes.io/ kubernetes-xenial main" | sudo tee /etc/apt/sources.list.d/kubernetes.list && \
    sudo curl https://baltocdn.com/helm/signing.asc | sudo apt-key add - && \
    echo "deb https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install -y apt-transport-https python3-pip gettext-base kubectl helm vagrant virtualbox yamllint && \
    pip3 install boto3 ansible-lint ansible==5.6.0 molecule==3.6.1 molecule-vagrant==1.0.0 && \
    curl https://get.datree.io | /bin/bash

RUN mkdir -p /etc/ansible
COPY .devcontainer/ansible.cfg /etc/ansible/ansible.cfg

USER vscode

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy install -r /tmp/requirements.yml
