import boto3

# ============================================================
# NET-001: Cleanup — Destroy all resources created by deploy.py
# ============================================================

PROFILE = 'rchiasro1'
REGION_A = 'us-east-1'
REGION_B = 'us-east-2'
BUCKET_A_NAME = 'net001-bucket-a-demo'
BUCKET_B_NAME = 'net001-bucket-b-demo'

session_a = boto3.Session(profile_name=PROFILE, region_name=REGION_A)
session_b = boto3.Session(profile_name=PROFILE, region_name=REGION_B)

s3_a = session_a.client('s3')
s3_b = session_b.client('s3')
ec2_a = session_a.client('ec2')


def empty_and_delete_bucket(s3_client, bucket_name, region):
    print(f"  Emptying {bucket_name} ({region})...")
    try:
        objects = s3_client.list_objects_v2(Bucket=bucket_name)
        if 'Contents' in objects:
            for obj in objects['Contents']:
                s3_client.delete_object(Bucket=bucket_name, Key=obj['Key'])
        s3_client.delete_bucket(Bucket=bucket_name)
        print(f"  Deleted {bucket_name}")
    except s3_client.exceptions.NoSuchBucket:
        print(f"  {bucket_name} does not exist, skipping")
    except Exception as e:
        print(f"  Error: {e}")


def delete_vpc_resources():
    print("\n=== Deleting VPC Resources (Region A) ===")

    vpcs = ec2_a.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': ['NET-001-VPC']}])

    if not vpcs['Vpcs']:
        print("  No NET-001-VPC found, skipping")
        return

    vpc_id = vpcs['Vpcs'][0]['VpcId']
    print(f"  Found VPC: {vpc_id}")

    endpoints = ec2_a.describe_vpc_endpoints(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
    )
    for ep in endpoints['VpcEndpoints']:
        if ep['VpcEndpointId'].startswith('vpce-'):
            ec2_a.delete_vpc_endpoints(VpcEndpointIds=[ep['VpcEndpointId']])
            print(f"  Deleted endpoint: {ep['VpcEndpointId']}")

    subnets = ec2_a.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    for subnet in subnets['Subnets']:
        ec2_a.delete_subnet(SubnetId=subnet['SubnetId'])
        print(f"  Deleted subnet: {subnet['SubnetId']}")

    ec2_a.delete_vpc(VpcId=vpc_id)
    print(f"  Deleted VPC: {vpc_id}")


def main():
    print("=" * 60)
    print("NET-001 CLEANUP")
    print("=" * 60)

    print("\n=== Deleting Buckets ===")
    empty_and_delete_bucket(s3_a, BUCKET_A_NAME, REGION_A)
    empty_and_delete_bucket(s3_b, BUCKET_B_NAME, REGION_B)

    delete_vpc_resources()

    print("\n" + "=" * 60)
    print("CLEANUP COMPLETE — All NET-001 resources destroyed")
    print("=" * 60)


if __name__ == '__main__':
    main()
