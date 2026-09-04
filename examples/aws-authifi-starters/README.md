# AWS Authifi starters

These examples use managed AWS hosting so an application can reach a working
Authifi login without managing an EC2 host, load balancer, certificate, or
deployment script.

| Example | Hosting | Client |
| --- | --- | --- |
| [React SPA](react-amplify-spa) | Amplify Hosting | Public client with PKCE |
| [Next.js BFF](nextjs-ecs-express) | ECS Express Mode | Confidential client |

The deployment walkthrough is in the [minimal AWS web app guide](../../docs/guides/minimal-aws-web-app-authifi.md).
