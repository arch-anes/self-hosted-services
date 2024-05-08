FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-22.04

ARG KUBECTL_VERSION=1.30

RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v$KUBECTL_VERSION/deb/Release.key | gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v$KUBECTL_VERSION/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list

RUN curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install -y apt-transport-https python3-pip gettext-base kubectl helm vagrant virtualbox yamllint && \
    pip3 install boto3 ansible-lint ansible molecule molecule-plugins[vagrant]

RUN mkdir -p /etc/ansible
COPY .devcontainer/ansible.cfg /etc/ansible/ansible.cfg

USER vscode

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy install -r /tmp/requirements.yml
