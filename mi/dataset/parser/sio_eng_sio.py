#!/usr/bin/env python

"""
@package mi.dataset.parser.sio_eng_sio_mule
@file marine-integrations/mi/dataset/parser/sio_eng_sio_mule.py
@author Mike Nicoletti
@brief Parser for the sio_eng_sio_mule dataset driver
Release notes:

Starting SIO Engineering Driver
"""

__author__ = 'Mike Nicoletti'
__license__ = 'Apache 2.0'

import re
import ntplib

from datetime import datetime

from mi.core.log import get_logger
log = get_logger()

from mi.core.common import BaseEnum
from mi.core.instrument.dataset_data_particle import DataParticle

from mi.core.exceptions import SampleException, UnexpectedDataException

from mi.dataset.parser.sio_mule_common import \
    SioParser, \
    SIO_HEADER_MATCHER, \
    SIO_HEADER_GROUP_ID, \
    SIO_HEADER_GROUP_TIMESTAMP

ENG_REGEX = r'\x01CS([0-9]{5})[0-9]{2}_[0-9A-Fa-f]{4}[a-zA-Z]([0-9A-Fa-f]{8})_' \
            '[0-9A-Fa-f]{2}_[0-9A-Fa-f]{4}\x02\n([-\d]+\.\d+) ' \
            '([-\d]+\.\d+) ([-\d]+) ([-\d]+) ([-\d]+)\n'
ENG_MATCHER = re.compile(ENG_REGEX)


class DataParticleType(BaseEnum):
    TELEMETERED = 'sio_eng_control_status'
    RECOVERED = 'sio_eng_control_status_recovered'


class SioEngSioParserDataParticleKey(BaseEnum):
    # sio_eng_control_status
    SIO_CONTROLLER_ID = "sio_controller_id"
    SIO_CONTROLLER_TIMESTAMP = "sio_controller_timestamp"
    SIO_VOLTAGE_STRING = 'sio_eng_voltage'
    SIO_TEMPERATURE_STRING = 'sio_eng_temperature'
    SIO_ON_TIME = 'sio_eng_on_time'
    SIO_NUMBER_OF_WAKEUPS = 'sio_eng_number_of_wakeups'
    SIO_CLOCK_DRIFT = 'sio_eng_clock_drift'


class SioEngSioDataParticle(DataParticle):
    """
    Abstract Class for particles from the sio_eng_sio_mule data set
    """

    @staticmethod
    def encode_int_16(hex_str):
        return int(hex_str, 16)

    def _build_parsed_values(self):
        """
        Take something in the data format and turn it into
        an array of dictionaries defining the data in the particle
        with the appropriate tag.
        """

        # raw_data will contain the match results of the ENG_REGEX
        match = self.raw_data

        result = [self._encode_value(SioEngSioParserDataParticleKey.SIO_CONTROLLER_ID, match.group(1), int),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_CONTROLLER_TIMESTAMP,
                                     match.group(2), SioEngSioDataParticle.encode_int_16),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_VOLTAGE_STRING, match.group(3), float),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_TEMPERATURE_STRING, match.group(4), float),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_ON_TIME, match.group(5), int),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_NUMBER_OF_WAKEUPS, match.group(6), int),
                  self._encode_value(SioEngSioParserDataParticleKey.SIO_CLOCK_DRIFT, match.group(7), int)]

        return result


class SioEngSioTelemeteredDataParticle(SioEngSioDataParticle):
    """
    Concrete Class for particles from the sio_eng_sio_mule data set
    """
    _data_particle_type = DataParticleType.TELEMETERED


class SioEngSioRecoveredDataParticle(SioEngSioDataParticle):
    """
    Concrete Class for particles from the sio_eng_sio recovered data set
    """
    _data_particle_type = DataParticleType.RECOVERED


class SioEngSioParser(SioParser):
    """
    Abstract Class for parsing Sio Eng Sio files
    """

    def parse_chunks(self):
        """
        Parse out any pending data chunks in the chunker. If
        it is a valid data piece, build a particle, update the position and
        timestamp. Go until the chunker has no more valid data.
        @retval a list of tuples with sample particles encountered in this
            parsing, plus the state. An empty list of nothing was parsed.
        """
        result_particles = []

        (nd_timestamp, non_data, non_start, non_end) = self._chunker.get_next_non_data_with_index(clean=False)
        (timestamp, chunk, start, end) = self._chunker.get_next_data_with_index(clean=True)

        # if there is any non data handle it
        self.handle_non_data(non_data, non_end, start)

        while chunk is not None:
            header_match = SIO_HEADER_MATCHER.match(chunk)

            if header_match.group(SIO_HEADER_GROUP_ID) == 'CS':
                data_match = ENG_MATCHER.match(chunk)
                if data_match:
                    # put timestamp from hex string to float:
                    posix_time = int(header_match.group(SIO_HEADER_GROUP_TIMESTAMP), 16)
                    log.debug('utc timestamp %s', datetime.utcfromtimestamp(posix_time))
                    timestamp = ntplib.system_to_ntp_time(float(posix_time))
                    # particle-ize the data block received, return the record
                    sample = self._extract_sample(self._particle_class, None, data_match, timestamp)
                    if sample:
                        # create particle
                        result_particles.append(sample)

                else:
                    log.warn('CS data does not match REGEX')
                    self._exception_callback(SampleException('CS data does not match REGEX'))

            # 'PS' IDs will also be in this file but are specifically ignored
            elif header_match.group(SIO_HEADER_GROUP_ID) != 'PS':
                message = 'Unexpected Sio Header ID %s' % header_match.group(SIO_HEADER_GROUP_ID)
                log.warn(message)
                self._exception_callback(UnexpectedDataException(message))

            (nd_timestamp, non_data, non_start, non_end) = self._chunker.get_next_non_data_with_index(clean=False)
            (timestamp, chunk, start, end) = self._chunker.get_next_data_with_index(clean=True)

            # if there is any non data handle it
            self.handle_non_data(non_data, non_end, start)

        return result_particles

    def handle_non_data(self, non_data, non_end, start):
        """
        Check for and handle any non-data that is found in the file
        """
        # If there is non-data it is an error
        if non_data is not None and non_end <= start:
            message = "Found %d bytes of un-expected non-data" % len(non_data)
            log.warn(message)
            # if non-data is a fatal error, directly call the exception, if it is not use the _exception_callback
            self._exception_callback(UnexpectedDataException(message))