FROM ghcr.io/arch-anes/dev-container-kubernetes-ansible:latest

RUN mkdir -p /etc/ansible
COPY .devcontainer/ansible.cfg /etc/ansible/ansible.cfg

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy install -r /tmp/requirements.yml
