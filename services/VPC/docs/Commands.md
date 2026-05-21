# VPC — CLI Commands Reference

## VPC

```bash
aws ec2 describe-vpcs
aws ec2 describe-vpcs --vpc-ids vpc-xxx
aws ec2 create-vpc --cidr-block 10.0.0.0/16
aws ec2 delete-vpc --vpc-id vpc-xxx
aws ec2 modify-vpc-attribute --vpc-id vpc-xxx --enable-dns-hostnames
aws ec2 associate-vpc-cidr-block --vpc-id vpc-xxx --cidr-block 10.1.0.0/16
aws ec2 disassociate-vpc-cidr-block --association-id vpc-cidr-assoc-xxx
```

## Subnets

```bash
aws ec2 describe-subnets --filters "Name=vpc-id,Values=vpc-xxx"
aws ec2 create-subnet --vpc-id vpc-xxx --cidr-block 10.0.1.0/24 --availability-zone us-east-1a
aws ec2 delete-subnet --subnet-id subnet-xxx
```

## Route Tables

```bash
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=vpc-xxx"
aws ec2 create-route --route-table-id rtb-xxx --destination-cidr-block 0.0.0.0/0 --gateway-id igw-xxx
aws ec2 create-route --route-table-id rtb-xxx --destination-cidr-block 10.0.0.0/8 --transit-gateway-id tgw-xxx
aws ec2 associate-route-table --route-table-id rtb-xxx --subnet-id subnet-xxx
```

## Security Groups

```bash
aws ec2 describe-security-groups --group-ids sg-xxx
aws ec2 authorize-security-group-ingress --group-id sg-xxx --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 revoke-security-group-ingress --group-id sg-xxx --protocol tcp --port 443 --cidr 0.0.0.0/0
```

## VPC Endpoints

```bash
aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=vpc-xxx"
aws ec2 create-vpc-endpoint --vpc-id vpc-xxx --service-name com.amazonaws.us-east-1.s3 --vpc-endpoint-type Gateway --route-table-ids rtb-xxx
aws ec2 create-vpc-endpoint --vpc-id vpc-xxx --service-name com.amazonaws.us-east-1.ssm --vpc-endpoint-type Interface --subnet-ids subnet-xxx --private-dns-enabled
aws ec2 delete-vpc-endpoints --vpc-endpoint-ids vpce-xxx
```

## VPC Flow Logs

```bash
aws ec2 create-flow-log --resource-type VPC --resource-id vpc-xxx --traffic-type ALL --log-destination-type cloud-watch-logs --log-group-name /vpc/flowlogs
aws ec2 describe-flow-logs --filter "Name=resource-id,Values=vpc-xxx"
aws ec2 delete-flow-logs --flow-log-ids fl-xxx
```

## Network ACLs

```bash
aws ec2 describe-network-acls --filters "Name=vpc-id,Values=vpc-xxx"
aws ec2 create-network-acl-entry --network-acl-id acl-xxx --rule-number 100 --protocol tcp --port-range From=443,To=443 --cidr-block 0.0.0.0/0 --rule-action allow --ingress
```

## Peering

```bash
aws ec2 create-vpc-peering-connection --vpc-id vpc-xxx --peer-vpc-id vpc-yyy --peer-region us-west-2
aws ec2 accept-vpc-peering-connection --vpc-peering-connection-id pcx-xxx
aws ec2 describe-vpc-peering-connections
```

## Transit Gateway

```bash
aws ec2 describe-transit-gateways
aws ec2 create-transit-gateway-vpc-attachment --transit-gateway-id tgw-xxx --vpc-id vpc-xxx --subnet-ids subnet-xxx
aws ec2 describe-transit-gateway-route-tables --transit-gateway-id tgw-xxx
```
