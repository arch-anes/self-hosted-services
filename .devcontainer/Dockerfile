FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-20.04


RUN sudo curl -fsSLo /usr/share/keyrings/kubernetes-archive-keyring.gpg https://packages.cloud.google.com/apt/doc/apt-key.gpg && \
 echo "deb [signed-by=/usr/share/keyrings/kubernetes-archive-keyring.gpg] https://apt.kubernetes.io/ kubernetes-xenial main" | sudo tee /etc/apt/sources.list.d/kubernetes.list

RUN apt-get update && apt-get install -y python3-pip gettext-base kubectl && pip3 install ansible==3.4.0 mitogen==0.3.0rc1 boto3 ansible-lint

RUN mkdir -p /etc/ansible
COPY ansible.cfg /etc/ansible/ansible.cfg
