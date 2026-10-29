from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from promotions.giveaways import GiveawayError, close_giveaway
from promotions.models import GiveawayCampaign


class Command(BaseCommand):
    help = "Close active giveaway campaigns whose approved entry window has ended."

    def add_arguments(self, parser):
        parser.add_argument(
            "--operator-email",
            required=True,
            help="Email address of the authorized giveaway operator.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Close the matching campaigns. Without this flag, report only.",
        )

    def handle(self, *args, **options):
        operator_email = options["operator_email"].strip()
        operator = get_user_model().objects.filter(email__iexact=operator_email).first()
        if operator is None:
            raise CommandError("The specified operator account does not exist.")
        if not operator.has_perm("promotions.activate_giveaways"):
            raise CommandError("The specified operator cannot close giveaway campaigns.")

        expired_campaigns = GiveawayCampaign.objects.filter(
            status=GiveawayCampaign.Status.ACTIVE,
            ends_at__lte=timezone.now(),
        ).order_by("pk")
        if not options["apply"]:
            for campaign in expired_campaigns:
                self.stdout.write(f"Would close campaign {campaign.pk}: {campaign.name}")
            self.stdout.write("Dry run complete. Pass --apply to close these campaigns.")
            return

        closed_count = 0
        for campaign in expired_campaigns:
            try:
                close_giveaway(campaign, actor=operator)
            except GiveawayError as error:
                self.stderr.write(f"Skipped campaign {campaign.pk}: {error}")
            else:
                closed_count += 1
                self.stdout.write(f"Closed campaign {campaign.pk}: {campaign.name}")
        self.stdout.write(f"Closed {closed_count} expired campaign(s).")
