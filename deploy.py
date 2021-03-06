#!/usr/bin/env python3

# include standard modules
import os
import signal
import sys


# include 3rd party modules
import boto3
from botocore.exceptions import ClientError
from colorama import init, Fore
from halo import Halo

# include custom modules
import key_gen
import dev_env

def main(domain=None, email=None):
    """Create/Update/Delete CloudFormation stack to deploy S3 static website.

    Launches two CloudFormation stacks that create:
        - Three S3 buckets
        - Two CloudFront distributions
        - Three IAM inline policies
        - Two Route 53 DNS records
        - Two IAM roles
        - One IAM group
        - One IAM user
        - One ACM SSL/TLS certificate
        - One RSA key pair
        - One CodeCommit repository
        - One CodePipeline pipeline
        - One CodeBuild project

    Resources will be created in region us-east-1, for ACM
    certificate/CloudFront compatibility.

    Script checks for existing stack and if found, prompts to
    push CloudFormation template changes via an update or rollback deployment
    via a delete.
    """

    print(Fore.WHITE + '\n### Static Site Install ###' + Fore.RESET)

    if 'domain' not in locals():
        domain = input(Fore.GREEN + '\nEnter domain name for static site: '
            + Fore.RESET)
    if 'email' not in locals():
        email = input(Fore.GREEN + '\nEnter email address for notifications: '
            + Fore.RESET)
    home = os.path.expanduser('~/')
    site_path = home + domain
    region = 'us-east-1' # overide any local AWS config; needed for ACM cert
    repo_base = 'git-codecommit.us-east-1.amazonaws.com'
    repo_https = 'https://' + repo_base + '/v1/repos/' + domain
    repo_ssh = 'ssh://' + repo_base + '/v1/repos/' + domain
    site = 'Static-Site'
    stack_site = site
    stack_cicd = site + '-CICD'
    stacks = [site, stack_cicd]

    account = boto3.client('sts').get_caller_identity()['Account']
    s3 = boto3.resource('s3', region_name=region)
    cf = boto3.client('cloudformation', region_name=region)

    for stack in stacks:
        params = [{"ParameterKey": "DomainName","ParameterValue": domain}]

        if stack == stack_site:
            deploy_tpl = './cfn/site.cfn.yaml'
            params = params + [
                {"ParameterKey": "SiteName","ParameterValue": stack_site}
            ]
        if stack == stack_cicd:
            deploy_tpl = './cfn/cicd.cfn.yaml'
            params = params + [
                {"ParameterKey": "RepoURL","ParameterValue": repo_https},
                {"ParameterKey": "EmailAddress","ParameterValue": email},
                {"ParameterKey": "SiteName","ParameterValue": stack_cicd}
            ]

        try:
            cf.describe_stacks(StackName=stack)
        except ClientError as e:
            if e.response['Error']['Message'].endswith('does not exist'):
                launch_stack(
                    cf, deploy_tpl, domain, params, s3, stack_site, stack
                )
        else:
            print(Fore.YELLOW + '\nExisting CloudFormation stack found:',
                Fore.YELLOW + stack + '\n')

            prompt = Fore.GREEN + 'Skip, Update or Delete (S/U/D)? ' + \
                Fore.RESET

            while True:
                reply = str(input(prompt)).lower()
                if reply[:1] == 'u':
                    update_stack(cf, deploy_tpl, params, stack)
                    break
                elif reply[:1] == 'd':
                    if input(Fore.RED + '\nAre you sure you want to delete'
                        ' stack: ' + stack + ' (y/n)? ' +
                        Fore.RESET) == "y":
                        delete_stack(cf, domain, region, s3, stack_site, stack)
                    break
                elif reply[:1] == 's':
                    break
                else:
                    print(Fore.RED + '\nInvalid... only S, U or D!\n')

    key_gen.main()

    dev_env.main(cf, domain, email, home, repo_ssh, site_path, stack_cicd)

def launch_stack(cf, deploy_tpl, domain, params, s3, stack_site, stack):
    if stack == stack_site:
        print(Fore.GREEN +
            'Multiple certificate validation emails will be sent to:\n\n'
            '  - WHOIS listed domain contacts\n'
            '  - Administrator|hostmaster|postmaster|webmaster|admin'
            '@your_domain_name\n\n'
            'Click the approval link in ONE in order to finish deployment.'
        )
        wait = input(Fore.YELLOW + '\nPress enter to continue...\n')

    with open(deploy_tpl, 'r') as f:
        tmp_tpl = f.read()

    try:
        cf.create_stack(
            StackName=stack,
            TemplateBody=tmp_tpl,
            Parameters=params,
            Capabilities=['CAPABILITY_NAMED_IAM'],
            OnFailure='ROLLBACK'
        )

        spinner = Halo(text='Launching: '+ stack, color='green').start()
        waiter = cf.get_waiter('stack_create_complete')
        waiter.wait(StackName=stack)
        spinner.succeed(text='Deployed: '+ stack)
    except ClientError as e:
        error_string = 'Waiter encountered a terminal failure state'
        if e.response['Error']['Message'].endswith(error_string):
            print(Fore.RED + error_string + '\nSee AWS web console')
        else:
            print(Fore.RED + e.response['Error']['Message'])

def update_stack(cf, deploy_tpl, params, stack):
    with open(deploy_tpl, 'r') as f:
        tmp_tpl = f.read()

    try:
        cf.update_stack(
            StackName=stack,
            TemplateBody=tmp_tpl,
            Parameters=params,
            Capabilities=['CAPABILITY_NAMED_IAM']
        )

        spinner = Halo(text='Updating: '+ stack, color='green').start()
        waiter = cf.get_waiter('stack_update_complete')
        waiter.wait(StackName=stack)
        spinner.succeed(text='Updated: '+ stack)
    except ClientError as e:
        error_string = 'No updates are to be performed.'
        if e.response['Error']['Message'].endswith(error_string):
            print(Fore.RED + '\n' + stack + ' => ' + error_string)
        else:
            print(Fore.RED + e.response['Error']['Message'])

def delete_stack(cf, domain, region, s3, stack_site, stack):
    if stack == stack_site:
        buckets = [domain, 'log.' + domain, 'www.' + domain]
        for b in buckets:
            try:
                s3.meta.client.head_bucket(Bucket=b)
            except ClientError as e:
                if e.response['Error']['Message'].endswith('does not exist'):
                    pass
            else:
                # Bucket can't be deleted unless empty
                bucket = s3.Bucket(b)
                bucket.objects.all().delete()

    cf.delete_stack(StackName=stack)

    spinner = Halo(text='Deleting: '+ stack, color='green').start()
    waiter = cf.get_waiter('stack_delete_complete')
    waiter.wait(StackName=stack)
    spinner.succeed(text='Deleted: '+ stack)

def sigint_handler(signum, frame):
    # handles KeyboardInterrupt (ctrl + c ) a little prettier
    print(Fore.WHITE + '\n\nGoodbye!' + Fore.RESET)
    sys.exit(0)

signal.signal(signal.SIGINT, sigint_handler)

if __name__ == '__main__':
    main()
