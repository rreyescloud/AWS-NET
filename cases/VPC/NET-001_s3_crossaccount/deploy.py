import boto3
import json

# ============================================================
# NET-001: S3 Private Cross-Account Cross-Region Connectivity
# ============================================================
# This script simulates the scenario using a SINGLE account
# (Isengard) with two regions to demonstrate the concept.
# For true cross-account, deploy bucket policy on Account B
# and IAM role on Account A separately.
# ============================================================

PROFILE = 'rchiasro1'
REGION_A = 'us-east-1'
REGION_B = 'us-east-2'
BUCKET_A_NAME = 'net001-bucket-a-demo'
BUCKET_B_NAME = 'net001-bucket-b-demo'
VPC_CIDR = '10.0.0.0/16'
SUBNET_CIDR = '10.0.1.0/24'

session_a = boto3.Session(profile_name=PROFILE, region_name=REGION_A)
session_b = boto3.Session(profile_name=PROFILE, region_name=REGION_B)

s3_a = session_a.client('s3')
s3_b = session_b.client('s3')
ec2_a = session_a.client('ec2')
sts = session_a.client('sts')

account_id = sts.get_caller_identity()['Account']


def create_buckets():
    print("=== Creating Buckets ===")

    s3_a.create_bucket(
        Bucket=BUCKET_A_NAME,
        CreateBucketConfiguration={'LocationConstraint': REGION_A} if REGION_A != 'us-east-1' else {}
    ) if REGION_A != 'us-east-1' else s3_a.create_bucket(Bucket=BUCKET_A_NAME)
    print(f"  Bucket A: {BUCKET_A_NAME} ({REGION_A})")

    s3_b.create_bucket(
        Bucket=BUCKET_B_NAME,
        CreateBucketConfiguration={'LocationConstraint': REGION_B}
    )
    print(f"  Bucket B: {BUCKET_B_NAME} ({REGION_B})")

    s3_a.put_object(Bucket=BUCKET_A_NAME, Key='test-file.txt', Body=b'Hello from Bucket A')
    print("  Uploaded test-file.txt to Bucket A")


def create_vpc_with_endpoint():
    print("\n=== Creating VPC with S3 Gateway Endpoint (Region A) ===")

    vpc = ec2_a.create_vpc(CidrBlock=VPC_CIDR)
    vpc_id = vpc['Vpc']['VpcId']
    ec2_a.create_tags(Resources=[vpc_id], Tags=[{'Key': 'Name', 'Value': 'NET-001-VPC'}])
    print(f"  VPC: {vpc_id}")

    subnet = ec2_a.create_subnet(VpcId=vpc_id, CidrBlock=SUBNET_CIDR, AvailabilityZone=f'{REGION_A}a')
    subnet_id = subnet['Subnet']['SubnetId']
    ec2_a.create_tags(Resources=[subnet_id], Tags=[{'Key': 'Name', 'Value': 'NET-001-Subnet'}])
    print(f"  Subnet: {subnet_id}")

    rt = ec2_a.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
    rt_id = rt['RouteTables'][0]['RouteTableId']

    endpoint = ec2_a.create_vpc_endpoint(
        VpcId=vpc_id,
        ServiceName=f'com.amazonaws.{REGION_A}.s3',
        RouteTableIds=[rt_id],
        VpcEndpointType='Gateway',
        TagSpecifications=[{
            'ResourceType': 'vpc-endpoint',
            'Tags': [{'Key': 'Name', 'Value': 'NET-001-S3-Gateway-Endpoint'}]
        }]
    )
    endpoint_id = endpoint['VpcEndpoint']['VpcEndpointId']
    print(f"  S3 Gateway Endpoint: {endpoint_id}")

    return vpc_id, subnet_id, endpoint_id


def configure_bucket_policy():
    print("\n=== Configuring Cross-Account Bucket Policy (Bucket B) ===")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCrossAccountAccess",
                "Effect": "Allow",
                "Principal": {
                    "AWS": f"arn:aws:iam::{account_id}:root"
                },
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket"
                ],
                "Resource": [
                    f"arn:aws:s3:::{BUCKET_B_NAME}",
                    f"arn:aws:s3:::{BUCKET_B_NAME}/*"
                ]
            },
            {
                "Sid": "DenyNonSSL",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [
                    f"arn:aws:s3:::{BUCKET_B_NAME}",
                    f"arn:aws:s3:::{BUCKET_B_NAME}/*"
                ],
                "Condition": {
                    "Bool": {
                        "aws:SecureTransport": "false"
                    }
                }
            }
        ]
    }

    s3_b.put_bucket_policy(Bucket=BUCKET_B_NAME, Policy=json.dumps(policy))
    print(f"  Bucket policy applied to {BUCKET_B_NAME}")
    print(f"  - Cross-account access from {account_id}")
    print(f"  - TLS enforced (deny non-SSL)")


def test_cross_region_access():
    print("\n=== Testing Cross-Region Access (Account A → Bucket B) ===")

    s3_b_from_a = session_a.client('s3', region_name=REGION_B)

    s3_b_from_a.put_object(
        Bucket=BUCKET_B_NAME,
        Key='cross-region-test.txt',
        Body=b'Written from Region A to Bucket B in Region B'
    )
    print(f"  Successfully wrote to {BUCKET_B_NAME} from {REGION_A} session")

    response = s3_b_from_a.get_object(Bucket=BUCKET_B_NAME, Key='cross-region-test.txt')
    content = response['Body'].read().decode()
    print(f"  Successfully read from {BUCKET_B_NAME}: '{content}'")


def main():
    print(f"Account: {account_id}")
    print(f"Profile: {PROFILE}")
    print(f"Region A: {REGION_A} | Region B: {REGION_B}")
    print("=" * 60)

    create_buckets()
    vpc_id, subnet_id, endpoint_id = create_vpc_with_endpoint()
    configure_bucket_policy()
    test_cross_region_access()

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"\nResources created:")
    print(f"  - Bucket A: {BUCKET_A_NAME} ({REGION_A})")
    print(f"  - Bucket B: {BUCKET_B_NAME} ({REGION_B})")
    print(f"  - VPC: {vpc_id} ({REGION_A})")
    print(f"  - Subnet: {subnet_id}")
    print(f"  - S3 Gateway Endpoint: {endpoint_id}")
    print(f"\nRun cleanup.py to destroy all resources.")


if __name__ == '__main__':
    main()
