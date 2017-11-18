# -*- coding: utf-8 -*-
#
# This file is part of Bika LIMS
#
# Copyright 2011-2017 by it's authors.
# Some rights reserved. See LICENSE.txt, AUTHORS.txt.

import zLOG
import urllib
import transaction

from DateTime import DateTime
from Products.ATContentTypes.utils import DT2dt

from zope.component import getAdapters
from zope.component import getUtility

from bika.lims import api
from bika.lims import logger
from bika.lims.interfaces import IIdServer
from bika.lims import bikaMessageFactory as _
from bika.lims.numbergenerator import INumberGenerator


class IDServerUnavailable(Exception):
    pass


def idserver_generate_id(context, prefix, batch_size=None):
    """ Generate a new id using external ID server.
    """
    plone = context.portal_url.getPortalObject()
    url = api.get_bika_setup().getIDServerURL()

    try:
        if batch_size:
            # GET
            f = urllib.urlopen('%s/%s/%s?%s' % (
                url,
                plone.getId(),
                prefix,
                urllib.urlencode({'batch_size': batch_size}))
            )
        else:
            f = urllib.urlopen('%s/%s/%s' % (url, plone.getId(), prefix))
        new_id = f.read()
        f.close()
    except:
        from sys import exc_info
        info = exc_info()
        msg = 'generate_id raised exception: {}, {} \n ID server URL: {}'
        msg = msg.format(info[0], info[1], url)
        zLOG.LOG('INFO', 0, '', msg)
        raise IDServerUnavailable(_('ID Server unavailable'))

    return new_id


def get_objects_in_sequence(brain_or_object, ctype, cref):
    """Return a list of items
    """
    obj = api.get_object(brain_or_object)
    if ctype == "backreference":
        return get_backreferences(obj, cref)
    if ctype == "contained":
        return get_contained_items(obj, cref)
    raise RuntimeError("")


def get_backreferences(obj, relationship):
    """Returns the backreferences
    """
    return obj.getBackReferences(relationship)


def get_contained_items(obj, spec):
    """Returns a list of (id, subobject) tuples of the current context.
    If 'spec' is specified, returns only objects whose meta_type match 'spec'
    """
    return obj.objectItems(spec)


def get_config(context, **kw):
    """Fetch the config dict from the Bika Setup for the given portal_type
    """
    # get the ID formatting config
    config_map = api.get_bika_setup().getIDFormatting()

    # allow portal_type override
    portal_type = kw.get("portal_type") or api.get_portal_type(context)

    # check if we have a config for the given portal_type
    for config in config_map:
        if config['portal_type'] == portal_type:
            return config

    # return a default config
    default_config = {
        'form': '%s-{seq}' % portal_type.lower(),
        'sequence_type': 'generated',
        'prefix': '%s' % portal_type.lower(),
    }
    return default_config


def get_variables(context, **kw):
    """Prepares a dictionary of key->value pairs usable for ID formatting
    """

    # allow portal_type override
    portal_type = kw.get("portal_type") or api.get_portal_type(context)

    # The variables map hold the values that might get into the construced id
    variables = {
        'context': context,
        'id': api.get_id(context),
        'portal_type': portal_type,
        'year': get_current_year(),
        'parent': api.get_parent(context),
        'seq': 0,
    }

    # Augment the variables map depending on the portal type
    if portal_type == "AnalysisRequest":
        variables.update({
            'sampleId': context.getSample().getId(),
            'sample': context.getSample(),
        })

    elif portal_type == "SamplePartition":
        variables.update({
            'sampleId': context.aq_parent.getId(),
            'sample': context.aq_parent,
        })

    elif portal_type == "Sample":
        # get the prefix of the assigned sample type
        sample_id = context.getId()
        sample_type = context.getSampleType()
        sampletype_prefix = sample_type.getPrefix()

        date_now = DateTime()
        sampling_date = context.getSamplingDate()
        date_sampled = context.getDateSampled()

        # Try to get the date sampled and sampling date
        if sampling_date:
            samplingDate = DT2dt(sampling_date)
        else:
            # No Sample Date?
            logger.error("Sample {} has no sample date set".format(sample_id))
            # fall back to current date
            samplingDate = DT2dt(date_now)

        if date_sampled:
            dateSampled = DT2dt(date_sampled)
        else:
            # No Sample Date?
            logger.error("Sample {} has no sample date set".format(sample_id))
            dateSampled = DT2dt(date_now)

        variables.update({
            'clientId': context.aq_parent.getClientID(),
            'dateSampled': dateSampled,
            'samplingDate': samplingDate,
            'sampleType': sampletype_prefix,
        })

    return variables


def split(string, separator="-"):
    """ split a string on the given separator
    """
    if not isinstance(string, basestring):
        return []
    return string.split(separator)


def to_int(thing, default=0):
    """Convert a thing to an integer
    """
    try:
        return int(thing)
    except (TypeError, ValueError):
        return default


def slice(string, separator="-", start=None, end=None):
    """Slice out a segment of a string, which is splitted on separator.
    """

    # split the given string at the given separator
    segments = split(string, separator)

    # get the start and endposition for slicing
    length = len(segments)
    start = to_int(start)
    end = to_int(end, length)

    # return the sliced string
    return separator.join(segments[start:end])


def get_current_year():
    """Returns the current year as a two digit string
    """
    return DateTime().strftime("%Y")[2:]


def search_by_prefix(portal_type, prefix):
    """Returns brains which share the same portal_type and ID prefix
    """
    catalog = api.get_tool("portal_catalog")
    brains = catalog({"portal_type": portal_type})
    # Filter brains with the same ID prefix
    return filter(lambda brain: api.get_id(brain).startswith(prefix), brains)


def generateUniqueId(context, **kw):
    """ Generate pretty content IDs.
    """

    # allow portal_type override
    portal_type = kw.get("portal_type") or api.get_portal_type(context)

    # get the config for this portal type
    config = get_config(context, **kw)

    # get the variables map for string replacement
    variables = get_variables(context, **kw)

    # @mikejmets: Why this?
    # => Please remove if not used or write a comment here.
    if portal_type == "Sample" and kw.get("parent"):
        config = get_config('SamplePartition')
        variables.update({
            'sampleId': context.getId(),
            'sample': context,
        })

    # The new generated number
    number = 0

    # get the sequence type from the global config
    sequence_type = config.get("sequence_type", "generated")

    # The ID format for string interpolation
    id_template = config.get("form", "")

    # The split length defines where the variable part of the ID template begins
    split_length = config.get("split_length", 0)

    # The prefix tempalte is the static part of the ID
    prefix_template = slice(id_template, end=split_length)

    # Sequence Type is "Counter", so we use the length of the backreferences or
    # contained objects of the evaluated "context" defined in the config
    if sequence_type == 'counter':

        # This "context" is defined by the user in Bika Setup and can be actuall anything.
        # However, we assume it is something like "sample" or similar
        ctx = config.get("context")

        # get object behind the context name (falls back to the current context)
        obj = config.get(ctx, context)

        # get the counter type, which is either "backreference" or "contained"
        counter_type = config.get("counter_type")

        # the counter reference is either the "replationship" for
        # "backreference" or the meta type for contained objects
        counter_reference = config.get("counter_reference")

        # This should be a list of existing items, including the current context object
        seq_items = get_objects_in_sequence(obj, counter_type, counter_reference)

        # since the current context is already in the list of items, we need to -1
        number = len(seq_items) - 1

        # store the new number to the variables map for string interpolation
        variables["seq"] = number

    # Sequence Type is "Generated", so the ID is constructed according to the
    # configured split length
    if sequence_type == 'generated':

        # get the number generator
        number_generator = getUtility(INumberGenerator)

        # generate the key for the number generator storage
        prefix_config = '{}-{}'.format(portal_type.lower(), prefix_template)
        key = prefix_config.format(**variables)

        # XXX: Handle flushed storage - WIP!
        if key not in number_generator:
            # we need to figure out the current state of the DB.
            prefix = prefix_template.format(**variables)
            existing = search_by_prefix(portal_type, prefix)
            max_num = 1
            for brain in existing:
                num = to_int(slice(api.get_id(brain), start=split_length))
                if num > max_num:
                    max_num = num
            # set the number generator
            number_generator.set_number(key, max_num)

        # generate a new number
        number = number_generator.generate_number(key=key)

        # store the new number to the variables map for string interpolation
        variables["seq"] = number

    # string interpolate the given id template
    result = id_template.format(**variables)

    logger.info('generateUniqueId: %s' % api.normalize_filename(result))
    return api.normalize_filename(result)


def renameAfterCreation(obj):
    """Rename the content after it was created/added
    """
    # Check if the _bika_id was aready set
    bika_id = getattr(obj, "_bika_id", None)
    if bika_id is not None:
        return bika_id
    # Can't rename without a subtransaction commit when using portal_factory
    transaction.savepoint(optimistic=True)
    # The id returned should be normalized already
    new_id = None
    # Checking if an adapter exists for this content type. If yes, we will
    # get new_id from adapter.
    for name, adapter in getAdapters((obj, ), IIdServer):
        if new_id:
            logger.warn(('More than one ID Generator Adapter found for'
                         'content type -> %s') % obj.portal_type)
        new_id = adapter.generate_id(obj.portal_type)
    if not new_id:
        new_id = generateUniqueId(obj)

    parent = api.get_parent(obj)
    if new_id in parent.objectIds():
        # XXX We could do the check in a `while` loop and generate a new one.
        raise KeyError("The ID {} is already taken in the path {}".format(
            new_id, api.get_path(parent)))
    # rename the object to the new id
    parent.manage_renameObject(obj.id, new_id)

    return new_id
