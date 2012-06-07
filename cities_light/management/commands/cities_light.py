import urllib
import time
import os
import os.path
import logging
import zipfile
import optparse
import unicodedata

from django.core.management.base import BaseCommand
from django.utils.encoding import force_unicode

from ...exceptions import *
from ...signals import *
from ...models import *
from ...settings import *
from ...geonames import Geonames


class Command(BaseCommand):
    args = '''
[--force-all] [--force-import-all \\]
                              [--force-import countries.txt cities.txt ...] \\
                              [--force countries.txt cities.txt ...]
    '''.strip()
    help = '''
Download all files in CITIES_LIGHT_COUNTRY_SOURCES if they were updated or if
--force-all option was used.
Import country data if they were downloaded or if --force-import-all was used.

Same goes for CITIES_LIGHT_CITY_SOURCES.

It is possible to force the download of some files which have not been updated
on the server:

    manage.py --force cities15000.txt countryInfo.txt

It is possible to force the import of files which weren't downloaded using the
--force-import option:

    manage.py --force-import cities15000.txt countryInfo.txt
    '''.strip()

    logger = logging.getLogger('cities_light')

    option_list = BaseCommand.option_list + (
        optparse.make_option('--force-import-all', action='store_true',
            default=False, help='Import even if files are up-to-date.'
        ),
        optparse.make_option('--force-all', action='store_true', default=False,
            help='Download and import if files are up-to-date.'
        ),
        optparse.make_option('--force-import', action='append', default=[],
            help='Import even if files matching files are up-to-date'
        ),
        optparse.make_option('--force', action='append', default=[],
            help='Download and import even if matching files are up-to-date'
        ),
    )

    def handle(self, *args, **options):
        if not os.path.exists(DATA_DIR):
            self.logger.info('Creating %s' % DATA_DIR)
            os.mkdir(DATA_DIR)

        for url in SOURCES:
            destination_file_name = url.split('/')[-1]

            force = options['force_all'] or \
                destination_file_name in options['force']

            geonames = Geonames(url, force=force)
            downloaded = geonames.downloaded

            force_import = options['force_import_all'] or \
                destination_file_name in options['force_import']

            if downloaded or force_import:
                self.logger.info('Importing %s' % destination_file_name)

                if url in CITY_SOURCES:
                    self.city_import(geonames)
                elif url in REGION_SOURCES:
                    self.region_import(geonames)
                elif url in COUNTRY_SOURCES:
                    self.country_import(geonames)

    def _get_country(self, code2):
        '''
        Simple lazy identity map for code2->country
        '''
        if not hasattr(self, '_country_codes'):
            self._country_codes = {}

        if code2 not in self._country_codes.keys():
            self._country_codes[code2] = Country.objects.get(code2=code2)

        return self._country_codes[code2]

    def _get_region(self, country_code2, region_id):
        '''
        Simple lazy identity map for (country_code2, region_id)->region
        '''
        if not hasattr(self, '_region_codes'):
            self._region_codes = {}

        country = self._get_country(country_code2)
        if country.code2 not in self._region_codes:
            self._region_codes[country.code2] = {}

        if region_id not in self._region_codes[country.code2]:
            self._region_codes[country.code2][region_id] = Region.objects.get(
                country=country, geoname_id=region_id)

        return self._region_codes[country.code2][region_id]

    def country_import(self, geonames):
        for items in geonames.parse():
            try:
                country = Country.objects.get(code2=items[0])
            except Country.DoesNotExist:
                country = Country(code2=items[0])

            country.name = items[4]
            country.code3 = items[1]
            country.continent = items[8]
            country.tld = items[9][1:]  # strip the leading dot
            country.save()

    def region_import(self, geonames):
        for items in geonames.parse():
            try:
                region_items_pre_import.send(sender=self, items=items)
            except InvalidItems:
                continue

            code2, geoname_id = items[0].split('.')
            kwargs = dict(geoname_id=geoname_id,
                country=self._get_country(code2))

            try:
                region = Region.objects.get(**kwargs)
            except Region.DoesNotExist:
                region = Region(**kwargs)

            region.name = items[2]
            region.save()

    def city_import(self, geonames):
        for items in geonames.parse():
            try:
                city_items_pre_import.send(sender=self, items=items)
            except InvalidItems:
                continue

            kwargs = dict(name=items[1], country=self._get_country(items[8]))

            try:
                city = City.objects.get(**kwargs)
            except City.DoesNotExist:
                city = City(**kwargs)

            save = False
            if not city.region:
                city.region = self._get_region(items[8], items[10])
                save = True

            if not city.latitude:
                city.latitude = items[4]
                save = True

            if not city.longitude:
                city.longitude = items[5]
                save = True

            if not city.alternate_names:
                city.alternate_names = items[3]
                save = True

            if not city.geoname_id:
                # city may have been added manually
                city.geoname_id = items[0]
                save = True

            if save:
                city.save()
