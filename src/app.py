"""
Slack chat-bot Lambda handler.
"""

# Module Imports
import os
import logging
import json
import time
import hmac
import hashlib
from slackclient import SlackClient

# Local imports
import helpers
from version import __version__

# Get Environment Variables
# This is declared globally because as this is useful for tests etc.
SECRETS_NAME = os.environ["SECRETS_NAME"]
STAGE = os.environ["STAGE"]

# Set up logging here info so we should get the
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)

# Ignore non important logs from botocore and boto3 cause they talk to much
logging.getLogger('botocore').setLevel(logging.CRITICAL)
logging.getLogger('boto3').setLevel(logging.CRITICAL)


def lambda_handler(api_event, api_context):
    """Handle an incoming HTTP request from a Slack chat-bot.
    """

    # Grab secrets for the application.
    secrets = json.loads(helpers.get_secrets(SECRETS_NAME))
    if type(secrets) is not dict:
        raise TypeError("Secrets response must be a dictionary.")

    LOGGER.info(f" -- Startup Information Version: {__version__}")
    LOGGER.debug(f"Secret Information: {secrets}")
    LOGGER.info(f"Api Event: {api_event}")

    # Grab relevant information form the api_event
    slack_body_raw = api_event["body"]
    slack_body_dict = json.loads(slack_body_raw)
    request_headers = api_event["headers"]

    # If the stage is production make sure that we are receiving events from slack otherwise we don't care
    if STAGE is "prod":
        LOGGER.debug(f"We are in production. So we are going to verify the request.")
        if not verify_request(request_headers["X-Slack-Signature"], request_headers["X-Slack-Request-Timestamp"],
                              slack_body_raw, secrets["SIGNING_SECRET"]):
            return helpers.form_response(400, {"Error": "Bad Request Signature"})

    # This is to appease the slack challenge event that is sent
    # when subscribing to the slack event API. You can read more
    # here https://api.slack.com/events/url_verification
    if is_challenge(slack_body_dict):
        challenge_response_body = {
            "challenge": slack_body_dict["challenge"]
        }
        return helpers.form_response(200, challenge_response_body)

    # This parses the slack body dict to get the event JSON
    # this will hold information about the text and
    # the user who did it.
    slack_event_dict = slack_body_dict["event"]

    # Build the slack client. This allows us make slack API calls
    # read up on the python-slack-client here. We get this from
    # AWS secrets manager. https://github.com/slackapi/python-slackclient
    slack_client = SlackClient(secrets["BOT_TOKEN"])

    # We need to discriminate between events generated by 
    # the users, which we want to process and handle, 
    # and those generated by the bot.
    if "bot_id" in slack_body_dict:
        logging.warning("Ignore bot event")
    else:
        # Get the text of the message the user sent to the bot,
        # and reverse it.
        text = slack_event_dict["text"]
        reversed_text = text[::-1]

        # Get the ID of the channel where sthe message was posted.
        channel_id = slack_event_dict["channel"]

        # This makes the actual api call
        slack_client.api_call(
            "chat.postMessage",
            channel=channel_id,
            text=reversed_text
        )

    # Everything went fine return a good response.
    return helpers.form_response(200, {})


def is_challenge(slack_event_body: dict) -> bool:
    """Is the event a challenge from slack? If yes return the correct response to slack

    Args:
        slack_event_body (dict): The slack event JSON

    Returns:
        returns True if it is a slack challenge event returns False otherwise
    """
    if "challenge" in slack_event_body:
        LOGGER.info(f"Challenge Data: {slack_event_body['challenge']}")
        return True
    return False


def verify_request(slack_signature: str, slack_timestamp: str, slack_event_body: str, app_signing_secret) -> bool:
    """Does the header sent in the request match the secret token.

    If it doesn't it may be an insecure request from someone trying to pose as your
    application. You can read more about the url-verification and why this is necessary
    here https://api.slack.com/docs/verifying-requests-from-slack

    Args:
        app_signing_secret (str): The apps local signing secret that is given by slack to compare with formulated.
        slack_signature (str): The header of the http_request from slack X-Slack-Signature
        slack_timestamp (str): The header of the http_request from slack X-Slack-Request-Timestamp
        slack_event_body (str): The slack event body that must be formulated as a string

    Returns:
        A boolean. If True the request was valid if False request was not valid.
    """
    if abs(time.time() - float(slack_timestamp)) > 60 * 5:
        # The request is longer then 5 minutes ago
        LOGGER.warning(f"Request verification failed. Timestamp was over 5 mins old for the request")
        return False
    sig_basestring = f"v0:{slack_timestamp}:{slack_event_body}".encode('utf-8')
    slack_signing_secret = bytes(app_signing_secret, 'utf-8')
    my_signature = 'v0=' + hmac.new(slack_signing_secret, sig_basestring, hashlib.sha256).hexdigest()
    if hmac.compare_digest(my_signature, slack_signature):
        return True
    else:
        LOGGER.warning(f"Verification failed. my_signature: {my_signature} slack_signature: {slack_signature}")
        return False