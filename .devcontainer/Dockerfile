FROM mcr.microsoft.com/vscode/devcontainers/base:ubuntu-24.04

ARG KUBECTL_VERSION=1.33

RUN curl -fsSL https://pkgs.k8s.io/core:/stable:/v$KUBECTL_VERSION/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v$KUBECTL_VERSION/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list

RUN curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | sudo gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list

RUN curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list

RUN apt-get update && apt-get install -y apt-transport-https pipx python3-dev gettext-base kubectl helm vagrant qemu-kvm libvirt-daemon-system libvirt-dev kmod

# Workaround for https://github.com/pypa/pipx/issues/754#issuecomment-951162846
RUN PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install --include-deps ansible && \
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx inject  --include-deps ansible jmespath boto3 ansible-lint && \
    # Workaround for https://github.com/ansible-community/molecule-plugins/issues/301
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx inject  --include-deps ansible molecule==25.1.0 molecule-plugins==23.7.0 && \
    PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx inject ansible libvirt-python

ENV VAGRANT_DEFAULT_PROVIDER=libvirt
ENV LIBVIRT_DEFAULT_URI=qemu:///system

RUN libvirtd --daemon && virtlogd --daemon 

USER vscode

RUN vagrant plugin install vagrant-libvirt

RUN sudo adduser vscode libvirt
