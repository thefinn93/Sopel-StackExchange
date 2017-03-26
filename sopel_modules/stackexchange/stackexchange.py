# coding=utf-8

from __future__ import unicode_literals, absolute_import, division, print_function

from sopel.config.types import StaticSection, ValidatedAttribute
from sopel import module
import json
import logging
import requests
import time

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "StackExchange Question Monitor"}
API_BASE = "https://api.stackexchange.com/2.2"


class StackExchangeSection(StaticSection):
    clientid = ValidatedAttribute('clientid', int, default=9282)
    key = ValidatedAttribute('key', default="435HbS5X8U3C2pDD8Y*tXA((")
    token = ValidatedAttribute('token')


def setup(bot):
    bot.config.define_section('stackexchange', StackExchangeSection)


def configure(config):
    config.define_section('stackexchange', StackExchangeSection, validate=False)
    config.stackexchange.configure_setting('clientid', "What is your StackExchange App's Client ID?")
    config.stackexchange.configure_setting('key', "What is your StackExchange App's Key?")


def has_write_access(bot, trigger):
    if trigger.admin:
        return True
    return bot.privileges[trigger.sender].get(trigger.nick, 0) > 0


def get_subscriptions(bot, sender):
    current = bot.db.get_channel_value(sender, 'stackexchange_subscriptions')
    if current is None:
        return {}
    else:
        return json.loads(current)


def subscribe(bot, trigger):
    if not has_write_access(bot, trigger):
        return "Must be admin or channel op!"
    site = trigger.group(4)
    tag = trigger.group(5)
    logger.info("Subscription request from %s for %s on %s", trigger.sender, tag, site)
    if site is None or tag is None:
        return "Usage: %s subscribe site tag" % trigger.group(1)
    current = get_subscriptions(bot, trigger.sender)
    if site not in current:
        current[site] = []
    if tag not in current[site]:
        current[site].append(tag)
        bot.db.set_channel_value(trigger.sender, 'stackexchange_subscriptions', json.dumps(current))
        for db_key, question in get_questions(bot, trigger.sender):
            bot.db.set_channel_value(trigger.sender, db_key, time.time())
        return "Subscribed %s to %s on %s" % (trigger.sender, tag, site)
    else:
        return "%s is already subscribed to %s on %s" % (trigger.sender, tag, site)


def unsubscribe(bot, trigger):
    if not has_write_access(bot, trigger):
        return "Must be admin or channel op!"
    site = trigger.group(4)
    tag = trigger.group(5)
    logger.info("Unsubscribe request from %s for %s on %s", trigger.sender, tag, site)
    if site is None or tag is None:
        return "Usage: %s unsubscribe site tag" % trigger.group(1)
    current = get_subscriptions(bot, trigger.sender)
    if site not in current or tag not in current[site]:
        logger.info(current)
        return "%s is not subscribed to %s on %s (use %s list to see all subscriptions)" % (trigger.sender, tag, site,
                                                                                            trigger.group(1))
    else:
        current[site].remove(tag)
        bot.db.set_channel_value(trigger.sender, 'stackexchange_subscriptions', json.dumps(current))
        return "Unsubscribed %s from %s on %s" % (trigger.sender, tag, site)


def list_subscriptions(bot, trigger):
    subscriptions = get_subscriptions(bot, trigger.sender)
    noitems = True
    for site, tags in subscriptions.items():
        if len(tags) > 0:
            noitems = False
            bot.reply("Subscribed to tags %s on %s" % (", ".join(tags), site))
    if noitems:
        bot.reply("No subscriptions found for %s" % trigger.sender)


def shorten(link):
    link = link.split("/")
    return "/".join(link[:-1])


def get_questions(bot, channel):
    out = []
    subscriptions = get_subscriptions(bot, channel)
    for site in subscriptions:
        params = {
            "order": "desc",
            "sort": "creation",
            "tagged": ";".join(subscriptions[site]),
            "site": site,
            "access_token": bot.config.stackexchange.token,
            "key": bot.config.stackexchange.key
        }
        logger.info("Making request with params %s for %s", params, channel)
        response = requests.get("%s/search" % API_BASE, params=params, headers=HEADERS)
        if response.ok:
            questions = response.json()
            quota_remaining = questions.get("quota_remaining")
            quota_max = questions.get("quota_max")
            backoff = questions.get("backoff")
            logger.debug("Got %s questions. %.d%% (%d/%d) of our quota remaining.%s",
                         (len(questions.get("items"))), (quota_remaining/quota_max)*100, quota_remaining,
                         quota_max, "%s second backoff" % backoff if backoff else "")
            for question in questions.get("items", []):
                db_key = "stackexchange-posted-%d" % question.get("question_id")
                posted = bot.db.get_channel_value(channel, db_key)
                if not posted:
                    out.append((db_key, question))
        else:
            logger.warning("Request to StackExchange returned %s: %s", response.status_code, response.content)
    return out


@module.interval(60)
def check(bot):
    for channel in bot.channels:
        for db_key, question in get_questions(bot, channel):
            link = shorten(question.get("link"))
            answered = " [Answered]" if question.get("answered") else ""
            msg = "%s [%s]%s" % (question.get("title"), link, answered)
            bot.msg(channel, msg)
            bot.db.set_channel_value(channel, db_key, time.time())


@module.commands('stackexchange')
def stackexchange(bot, trigger):
    subcommands = {
        "subscribe": subscribe,
        "unsubscribe": unsubscribe,
        "list": list_subscriptions
    }
    subcommand = trigger.group(3)
    if subcommand in subcommands:
        result = subcommands[subcommand](bot, trigger)
        if result is not None:
            bot.reply(result)
    else:
        bot.reply('Invalid subcommand. Please use one of %s' % ", ".join(subcommands.keys()))
