FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-22.04

RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg && \
    echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list && \
    sudo curl https://baltocdn.com/helm/signing.asc | sudo apt-key add - && \
    echo "deb https://baltocdn.com/helm/stable/debian/ all main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN apt-get update && apt-get install -y apt-transport-https python3-pip gettext-base kubectl helm vagrant virtualbox yamllint && \
    pip3 install boto3 ansible-lint ansible molecule molecule-plugins[vagrant]

RUN mkdir -p /etc/ansible
COPY .devcontainer/ansible.cfg /etc/ansible/ansible.cfg

USER vscode

ENV ANSIBLE_HOST_KEY_CHECKING false

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy install -r /tmp/requirements.yml
