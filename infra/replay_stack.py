"""Replay on AWS, replay-only: the recorded runs, listed, opened, traced and diffed.

Two shapes, chosen with `-c cloudfront=true|false` (default false):

    cloudfront=true
      browser -> CloudFront -+- /*      -> S3 site bucket (private, OAC)
                             +- /api/*  -> HTTP API -> Lambda api
    cloudfront=false   (the account cannot create CloudFront resources until verified)
      browser -> HTTP API -+- ANY /api/{proxy+} -> Lambda api -> the router
                           +- $default          -> Lambda api -> web/dist, from the bundle

    either way: events table Streams -> Lambda projector -> writes views

The table keys are the ones store/dynamo.py and store/views.py declare: PK and
SK, both strings, no indexes. No model, no Bedrock: the api Lambda's runner is
live=False, so fork and resume answer 503 with the reason.

Cost guards for a credit account: on-demand tables, API throttled to 20 rps
(burst 40), 7-day logs, a USD 5 monthly budget alert, PriceClass_200, and every
resource destroyed with the stack. There is no reserved concurrency on the api
function: this account's concurrency limit is 10, and AWS refuses any
reservation that leaves fewer than 10 unreserved.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_budgets as budgets,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_lambda_event_sources as sources,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / ".scratch" / "lambda-bundle"
SITE = REPO / "web" / "dist"
BUNDLED_SITE = BUNDLE / "site"
ALERT_EMAIL = "kavalinikhilesh@gmail.com"

# Every path the app itself handles is served index.html; files keep their paths.
# Attached to the S3 behaviour only, so /api/* 404s reach the browser untouched.
SPA_REWRITE = """
function handler(event) {
  var request = event.request;
  var last = request.uri.split('/').pop();
  if (last.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}
"""


def _table(scope: Construct, name: str, stream: bool) -> dynamodb.Table:
    return dynamodb.Table(
        scope, name,
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
        sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
        billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        stream=dynamodb.StreamViewType.NEW_IMAGE if stream else None,
        removal_policy=cdk.RemovalPolicy.DESTROY,
    )


def _bucket(scope: Construct, name: str) -> s3.Bucket:
    return s3.Bucket(
        scope, name,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        encryption=s3.BucketEncryption.S3_MANAGED,
        enforce_ssl=True,
        removal_policy=cdk.RemovalPolicy.DESTROY,
        auto_delete_objects=True,
    )


class ReplayStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        use_cloudfront = str(self.node.try_get_context("cloudfront") or "false").lower() == "true"
        site_files = SITE if use_cloudfront else BUNDLED_SITE
        for required in (BUNDLE, site_files / "index.html"):
            if not required.exists():
                raise FileNotFoundError(f"{required} is missing: build web/ and run scripts/bundle_lambda.sh first")

        events = _table(self, "Events", stream=True)
        views = _table(self, "Views", stream=False)
        payloads = _bucket(self, "Payloads")

        code = lambda_.Code.from_asset(str(BUNDLE))
        environment = {"EVENTS_TABLE": events.table_name, "VIEWS_TABLE": views.table_name,
                       "PAYLOAD_BUCKET": payloads.bucket_name}

        def function(name: str, handler: str, timeout_s: int) -> lambda_.Function:
            log_group = logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_WEEK,
                                      removal_policy=cdk.RemovalPolicy.DESTROY)
            return lambda_.Function(
                self, name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=code,
                memory_size=512,
                timeout=cdk.Duration.seconds(timeout_s),
                environment=environment,
                log_group=log_group,
            )

        # api: reads only. Every route that would write drives the agent, and the
        # replay-only runner refuses those before anything is written.
        api_fn = function("Api", "replay.aws.api.handler", 10)
        events.grant_read_data(api_fn)
        views.grant_read_data(api_fn)
        payloads.grant_read(api_fn)

        # projector: reads the log, writes the views.
        projector_fn = function("Projector", "replay.aws.projector.handler", 60)
        events.grant_read_data(projector_fn)
        payloads.grant_read(projector_fn)
        views.grant_write_data(projector_fn)
        projector_fn.add_event_source(sources.DynamoEventSource(
            events,
            starting_position=lambda_.StartingPosition.TRIM_HORIZON,
            batch_size=100,
            bisect_batch_on_error=True,
            retry_attempts=3,
        ))

        # Without CloudFront the api function serves the frontend as well, from the
        # copy of web/dist in its bundle: $default catches every non-/api path.
        http_api = apigw.HttpApi(
            self, "HttpApi", create_default_stage=True,
            default_integration=None if use_cloudfront
            else integrations.HttpLambdaIntegration("SiteIntegration", api_fn),
        )
        http_api.add_routes(path="/api/{proxy+}", methods=[apigw.HttpMethod.ANY],
                            integration=integrations.HttpLambdaIntegration("ApiIntegration", api_fn))
        stage = http_api.default_stage.node.default_child
        stage.add_property_override("DefaultRouteSettings.ThrottlingRateLimit", 20)
        stage.add_property_override("DefaultRouteSettings.ThrottlingBurstLimit", 40)

        if use_cloudfront:
            site_url = self._cloudfront(http_api)
        else:
            site_url = http_api.api_endpoint

        self._budget()

        cdk.CfnOutput(self, "SiteUrl", value=site_url)
        cdk.CfnOutput(self, "ApiPath", value="/api")
        cdk.CfnOutput(self, "EventsTable", value=events.table_name)
        cdk.CfnOutput(self, "ViewsTable", value=views.table_name)
        cdk.CfnOutput(self, "PayloadBucket", value=payloads.bucket_name)

    def _cloudfront(self, http_api: apigw.HttpApi) -> str:
        """The site bucket, its upload, and one distribution in front of both origins."""
        site = _bucket(self, "Site")
        spa = cloudfront.Function(self, "SpaRewrite", code=cloudfront.FunctionCode.from_inline(SPA_REWRITE),
                                  runtime=cloudfront.FunctionRuntime.JS_2_0)
        distribution = cloudfront.Distribution(
            self, "SiteDistribution",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[cloudfront.FunctionAssociation(
                    function=spa, event_type=cloudfront.FunctionEventType.VIEWER_REQUEST)],
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(f"{http_api.api_id}.execute-api.{self.region}.amazonaws.com"),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    # Every query string and header but Host: API Gateway routes on its own host.
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
        )

        s3deploy.BucketDeployment(
            self, "DeploySite",
            sources=[s3deploy.Source.asset(str(SITE))],
            destination_bucket=site,
            distribution=distribution,
            distribution_paths=["/*"],
        )
        return f"https://{distribution.distribution_domain_name}"

    def _budget(self) -> None:
        budgets.CfnBudget(
            self, "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="replay-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=5, unit="USD"),
            ),
            notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type="ACTUAL", comparison_operator="GREATER_THAN",
                    threshold=80, threshold_type="PERCENTAGE"),
                subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=ALERT_EMAIL)],
            )],
        )
