import * as cdk from 'aws-cdk-lib';
import { EcsExpressStack } from '../lib/ecs-express-stack';

const app = new cdk.App();

new EcsExpressStack(app, 'AuthifiNextjsEcsExpress', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});
