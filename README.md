# Self-hosted services

## Dependencies

-   docker-compose (remote only)
-   ansible (controller only)
-   openssh
-   rsync

## Create an inventory

### Static

```yml
docker_swarm_manager:
  hosts:
    aws-small-instance:
      advertised_address: "42.42.42.42:2377"
      swarm_labels:
        - small
    raspi:
      advertised_address: "42.42.42.40:2378"
      swarm_labels:
        - local
        - small
    big-manager:
      advertised_address: "42.42.42.40:2379"
      swarm_labels:
        - local
        - big

docker_swarm_worker:
  hosts:
    big-worker:
      advertised_address: "42.42.42.40:2380"
      swarm_labels:
        - local
        - big
```

### Dynamic (AWS)

```yml
plugin: aws_ec2
regions:
  - us-east-1
  - us-east-2
filters:
  instance-state-name: running
  tag:Category:
    - home-cloud
```

## Create custom config config

Copy `config.json.example` to `config.json` and fill it with your values

## Deploy

-   Run `ansible-galaxy install -r requirements.yml`
-   Run `env EC2_ACCESS_KEY=some_key EC2_SECRET_KEY=some_other_key ansible-playbook -e @config.json -i inventory_static.yml -i inventory_ec2.yml 0*.yml`
