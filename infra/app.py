"""The CDK app: one stack, replay-only, in ap-south-1.

    cd infra && npx aws-cdk@2 synth | diff | deploy | destroy

Build the web app and the Lambda bundle first: the stack reads web/dist and
.scratch/lambda-bundle at synth time.
"""

import aws_cdk as cdk

from replay_stack import ReplayStack

ACCOUNT = "987432216505"
REGION = "ap-south-1"

app = cdk.App()
ReplayStack(app, "ReplayStack", env=cdk.Environment(account=ACCOUNT, region=REGION))
app.synth()
