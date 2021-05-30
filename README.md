# Self-hosted services

## Dependencies

-   docker-compose (remote only)
-   ansible (controller only)
-   openssh
-   rsync

## Create inventory

```yml
docker_swarm_manager:
  hosts:
    aws-small-instance:
      swarm_labels:
        - small
    raspi:
      swarm_labels:
        - local
        - small
    big-manager:
      swarm_labels:
        - local
        - big

docker_swarm_worker:
  hosts:
    big-worker:
      swarm_labels:
        - local
        - big

```

## Create custom config config

Copy `config.json.example` to `config.json` and fill it with your values

## Deploy

-   Run `ansible-galaxy install -r requirements.yml`
-   Run `ansible-playbook -e @config.json -i inventory.yml 0*.yml`
