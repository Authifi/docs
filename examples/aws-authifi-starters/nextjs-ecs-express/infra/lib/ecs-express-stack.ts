import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecrAssets from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export class EcsExpressStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const serviceName = 'authifi-nextjs-ecs';
    const authifiHost = this.node.tryGetContext('authifiHost');
    const authifiTenant = this.node.tryGetContext('authifiTenant');
    const authifiClientId = this.node.tryGetContext('authifiClientId');
    const authifiResource = this.node.tryGetContext('authifiResource');
    const appUrl = this.node.tryGetContext('appUrl');
    const deploymentVersion = String(
      this.node.tryGetContext('deploymentVersion') ?? '1',
    );

    if (!authifiHost || !authifiTenant || !authifiClientId) {
      throw new Error('Set authifiHost, authifiTenant, and authifiClientId in cdk.json.');
    }

    const image = new ecrAssets.DockerImageAsset(this, 'AppImage', {
      directory: path.join(__dirname, '../..'),
    });

    const clientSecret = new secretsmanager.Secret(this, 'AuthifiClientSecret', {
      generateSecretString: {
        passwordLength: 64,
        excludePunctuation: true,
      },
    });
    const sessionSecret = new secretsmanager.Secret(this, 'NextAuthSecret', {
      generateSecretString: {
        passwordLength: 64,
        excludePunctuation: true,
      },
    });

    const executionRole = new iam.Role(this, 'TaskExecutionRole', {
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });
    executionRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        'service-role/AmazonECSTaskExecutionRolePolicy',
      ),
    );
    image.repository.grantPull(executionRole);
    clientSecret.grantRead(executionRole);
    sessionSecret.grantRead(executionRole);

    const infrastructureRole = new iam.Role(this, 'InfrastructureRole', {
      assumedBy: new iam.ServicePrincipal('ecs.amazonaws.com'),
    });
    infrastructureRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        'service-role/AmazonECSInfrastructureRoleforExpressGatewayServices',
      ),
    );

    const runtimeEnvironmentVariables = [
      { name: 'AUTHIFI_HOST', value: authifiHost },
      { name: 'AUTHIFI_TENANT', value: authifiTenant },
      { name: 'AUTHIFI_CLIENT_ID', value: authifiClientId },
      { name: 'AUTHIFI_CONFIG_VERSION', value: deploymentVersion },
    ];
    if (authifiResource) {
      runtimeEnvironmentVariables.push({
        name: 'AUTHIFI_RESOURCE',
        value: authifiResource,
      });
    }
    if (appUrl) {
      runtimeEnvironmentVariables.push({ name: 'NEXTAUTH_URL', value: appUrl });
    }

    const service = new ecs.CfnExpressGatewayService(this, 'Service', {
      serviceName,
      infrastructureRoleArn: infrastructureRole.roleArn,
      executionRoleArn: executionRole.roleArn,
      cpu: '1024',
      memory: '2048',
      healthCheckPath: '/api/health',
      primaryContainer: {
        image: image.imageUri,
        containerPort: 3000,
        environment: runtimeEnvironmentVariables,
        secrets: [
          {
            name: 'AUTHIFI_CLIENT_SECRET',
            valueFrom: clientSecret.secretArn,
          },
          { name: 'NEXTAUTH_SECRET', valueFrom: sessionSecret.secretArn },
        ],
      },
      tags: [{ key: 'Application', value: 'authifi-nextjs-starter' }],
    });

    new cdk.CfnOutput(this, 'AppUrl', {
      value: service.attrEndpoint,
    });
    new cdk.CfnOutput(this, 'ServiceArn', { value: service.attrServiceArn });
    new cdk.CfnOutput(this, 'AuthifiClientSecretArn', {
      value: clientSecret.secretArn,
    });
  }
}
