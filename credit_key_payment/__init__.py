# Part of Odoo. See LICENSE file for full copyright and licensing details.
from . import controllers
from . import models
from . import wizard
from odoo.addons.payment import setup_provider, reset_payment_provider


def post_init_hook(env):
    setup_provider(env, "credit_key")


def uninstall_hook(env):
    reset_payment_provider(env, "credit_key")
