# Copyright Notice:
# Copyright 2016, 2017, 2018 Distributed Management Task Force, Inc. All rights reserved.
# License: BSD 3-Clause License. For full text see link: https://github.com/DMTF/Redfish-Tools/blob/master/LICENSE.md

"""
File: doc_formatter.py

Brief : Contains DocFormatter class

Initial author: Second Rise LLC.
"""

import os
import copy
import re
import warnings
import sys
import functools
from doc_gen_util import DocGenUtilities
from format_utils import FormatUtils

class DocFormatter:
    """Generic class for schema documentation formatter"""

    def __init__(self, property_data, traverser, config, level=0):
        """Set up the markdown generator.

        property_data: pre-processed schemas.
        traverser: SchemaTraverser object
        config: configuration dict
        """
        self.property_data = property_data
        self.common_properties = {}
        self.traverser = traverser
        self.config = config
        self.level = level
        self.this_section = None
        self.current_version = {} # marker for latest version within property we're displaying.
        self.current_depth = 0
        self.sections = []
        self.registry_sections = []
        self.collapse_list_of_simple_type = True
        self.formatter = FormatUtils() # Non-markdown formatters will override this.

        # Get a list of schemas that will appear in the documentation. We need this to know
        # when to create an internal link, versus a link to a URI.
        self.documented_schemas = []
        schemas = [x for x in property_data.keys()]
        for schema_ref in schemas:
            details = self.property_data[schema_ref]
            if self.skip_schema(details['schema_name']):
                continue
            if len(details['properties']):
                self.documented_schemas.append(schema_ref)

        self.uri_match_keys = None
        if self.config.get('uri_replacements'):
            map_keys = list(self.config['uri_replacements'].keys())
            map_keys.sort(key=len, reverse=True)
            self.uri_match_keys = map_keys

        self.separators = {
            'inline': ', ',
            'linebreak': '\n'
            }

        # Properties to carry through from parent when a ref is extended:
        self.parent_props = ['description', 'longDescription', 'fulldescription_override', 'pattern', 'readonly', 'prop_required', 'prop_required_on_create', 'required_parameter']


    def emit(self):
        """ Output contents thus far """
        raise NotImplementedError


    def add_section(self, text, link_id=False):
        """ Add a top-level heading """
        raise NotImplementedError


    def add_description(self, text):
        """ Add the schema description """
        raise NotImplementedError


    def add_uris(self, uris):
        """ Add the uris """
        raise NotImplementedError


    def format_uri(self, uri):
        """ Format a URI for output. """
        # This is highlighting for markdown. Override for other output.
        uri_parts = uri.split('/')
        uri_parts_highlighted = []
        for part in uri_parts:
            if part.startswith('{') and part.endswith('}'):
                part = self.formatter.italic(part)
            uri_parts_highlighted.append(part)
        uri_highlighted = '/'.join(uri_parts_highlighted)
        return uri_highlighted


    def add_action_details(self, action_details):
        """ Add the action details (which should already be formatted) """
        if 'action_details' not in self.this_section:
            self.this_section['action_details'] = []
        self.this_section['action_details'].append(action_details)


    def add_profile_conditional_details(self, conditional_details):
        """ Add the conditional requirements for the profile (which should already be formatted) """
        if 'profile_conditional_details' not in self.this_section:
            self.this_section['profile_conditional_details'] = []
        self.this_section['profile_conditional_details'].append(conditional_details)


    def add_json_payload(self, json_payload):
        """ Add a JSON payload for the current section """
        raise NotImplementedError


    def add_property_row(self, formatted_row):
        """Add a row (or group of rows) for an individual property in the current section/schema.

        formatted_row should be a chunk of text already formatted for output"""
        raise NotImplementedError

    def add_property_details(self, formatted_details):
        """Add a chunk of property details information for the current section/schema."""
        raise NotImplementedError


    def add_registry_reqs(self, registry_reqs):
        """Add registry messages. registry_reqs includes profile annotations."""
        raise NotImplementedError


    def format_property_row(self, schema_ref, prop_name, prop_info, prop_path=[], in_array=False):
        """Format information for a single property. Returns an object with 'row' and 'details'.

        'row': content for the main table being generated.
        'details': content for the Property Details section.

        This may include embedded objects with their own properties.
        """
        raise NotImplementedError


    def format_property_details(self, prop_name, prop_type, prop_description, enum, enum_details,
                                supplemental_details, meta, anchor=None, profile={}):
        """Generate a formatted table of enum information for inclusion in Property Details."""
        raise NotImplementedError


    def format_list_of_object_descrs(self, schema_ref, prop_items, prop_path):
        """Format a (possibly nested) list of embedded objects.

        We expect this to amount to one definition, usually for 'items' in an array."""

        if isinstance(prop_items, dict):
            if 'properties' in prop_items:
                return self.format_object_descr(schema_ref, prop_items, prop_path)
            else:
                return self.format_non_object_descr(schema_ref, prop_items, prop_path)

        rows = []
        details = {}
        if isinstance(prop_items, list):
            for prop_item in prop_items:
                formatted = self.format_list_of_object_descrs(schema_ref, prop_item, prop_path)
                rows.extend(formatted['rows'])
                details.update(formatted['details'])
            return ({'rows': rows, 'details': details})

        return None

    def format_action_details(self, prop_name, action_details):
        """Generate a formatted Actions section from supplemental markdown."""
        raise NotImplementedError


    def format_action_parameters(self, schema_ref, prop_name, prop_descr, action_parameters):
        """Generate a formatted Actions section from parameters data"""
        raise NotImplementedError


    def format_base_profile_access(self, formatted_details):
        """Massage profile read/write requirements for display"""

        if formatted_details.get('is_in_profile'):
            profile_access = self._format_profile_access(read_only=formatted_details.get('read_only', False),
                                                         read_req=formatted_details.get('profile_read_req'),
                                                         write_req=formatted_details.get('profile_write_req'),
                                                         min_count=formatted_details.get('profile_mincount'))
        else:
            profile_access = ''

        return profile_access


    def format_conditional_access(self, conditional_req):
        """Massage conditional profile read/write requirements."""

        profile_access = self._format_profile_access(read_req=conditional_req.get('ReadRequirement'),
                                                     write_req=conditional_req.get('WriteRequirement'),
                                                     min_count=conditional_req.get('MinCount'))
        return profile_access


    def _format_profile_access(self, read_only=False, read_req=None, write_req=None, min_count=None):
        """Common formatting logic for profile_access column"""

        profile_access = ''
        if not self.config.get('profile_mode'):
            return profile_access

        # Each requirement  may be Mandatory, Recommended, IfImplemented, Conditional, or (None)
        if not read_req:
            read_req = 'Mandatory' # This is the default if nothing is specified.
        if read_only:
            profile_access = self.formatter.nobr(self.text_map(read_req)) + ' (Read-only)'
        elif read_req == write_req:
            profile_access = self.formatter.nobr(self.text_map(read_req)) + ' (Read/Write)'
        elif not write_req:
            profile_access = self.formatter.nobr(self.text_map(read_req)) + ' (Read)'
        else:
            # Presumably Read is Mandatory and Write is Recommended; nothing else makes sense.
            profile_access = (self.formatter.nobr(self.text_map(read_req)) + ' (Read)' + self.br() +
                              self.formatter.nobr(self.text_map(write_req)) + ' (Read/Write)')

        if min_count:
            if profile_access:
                profile_access += self.br()

            profile_access += self.formatter.nobr("Minimum " + str(min_count))

        return profile_access


    def format_conditional_details(self, schema_ref, prop_name, conditional_reqs):
        """Generate a formatted Conditional Details section from profile data"""
        formatted = []
        anchor = schema_ref + '|conditional_reqs|' + prop_name

        formatted.append(self.formatter.head_four(prop_name, self.level, anchor))

        rows = []
        for creq in conditional_reqs:
            req_desc = ''
            purpose = creq.get('Purpose', self.formatter.nbsp()*10)
            subordinate_to = creq.get('SubordinateToResource')
            compare_property = creq.get('CompareProperty')
            comparison = creq.get('Comparison')
            values = creq.get('Values')
            req = self.format_conditional_access(creq)

            if creq.get('BaseRequirement'):
                # Don't output the base requirement
                continue

            elif subordinate_to:
                req_desc = 'Resource instance is subordinate to ' + ' from '.join('"' + x + '"' for x in subordinate_to)

            if compare_property:
                compare_to = creq.get('CompareType', '')
                if compare_to in ['Equal', 'LessThanOrEqual', 'GreaterThanOrEqual', 'NotEqual']:
                    compare_to += ' to'

                compare_values = creq.get('CompareValues')
                if compare_values:
                    compare_values = ', '.join(['"' + x + '"' for x in compare_values])

                if req_desc:
                    req_desc += ' and '
                req_desc += '"' + compare_property + '"' + ' is ' + compare_to

                if compare_values:
                    req_desc += ' ' + compare_values

                if comparison and len(values):
                    req += ', must be ' + comparison + ' ' + ', '.join(['"' + val + '"' for val in values])
            rows.append(self.formatter.make_row([req_desc, req, purpose]))

        formatted.append(self.formatter.make_table(rows))

        return "\n".join(formatted)


    def append_unique_values(self, value_list, target_list):
        """ Unwind possibly-nested list, producing a list of unique strings found. """

        for val in value_list:
            if isinstance(val, list):
                self.append_unique_values(val, target_list)
            else:
                if val and val not in target_list:
                    target_list.append(val)


    def output_document(self):
        """Return full contents of document"""
        body = self.emit()
        return body


    def generate_output(self):
        """Generate formatted from schemas and supplemental data.

        Iterates through property_data and traverses schemas for details.
        Format of output will depend on the format_* methods of the class.
        """
        property_data = self.property_data
        traverser = self.traverser
        config = self.config
        schema_supplement = config.get('schema_supplement', {})
        profile_mode = config.get('profile_mode')

        schema_keys = self.documented_schemas
        schema_keys.sort(key=str.lower)

        for schema_ref in schema_keys:
            details = property_data[schema_ref]
            schema_name = details['schema_name']
            profile = config.get('profile_resources', {}).get(schema_ref, {})

            # Look up supplemental details for this schema/version
            version = details.get('latest_version', '1')
            major_version = version.split('.')[0]
            schema_key = schema_name + '_' + major_version
            supplemental = schema_supplement.get(schema_key,
                                                 schema_supplement.get(schema_name, {}))

            definitions = details['definitions']

            if config.get('omit_version_in_headers'):
                section_name = schema_name
            else:
                section_name = details['name_and_version']
            self.add_section(section_name, schema_name)
            self.current_version = {}

            uris = details['uris']

            # Normative docs prefer longDescription to description
            if config.get('normative') and 'longDescription' in definitions[schema_name]:
                description = definitions[schema_name].get('longDescription')
            else:
                description = definitions[schema_name].get('description')

            required = definitions[schema_name].get('required', [])
            required_on_create = definitions[schema_name].get('requiredOnCreate', [])

            # Override with supplemental schema description, if provided
            # If there is a supplemental Description or Schema-Intro, it replaces
            # the description in the schema. If both are present, the Description
            # should be output, followed by the Schema-Intro.
            if supplemental.get('description') and supplemental.get('schema-intro'):
                description = (supplemental.get('description') + '\n\n' +
                               supplemental.get('schema-intro'))
            elif supplemental.get('description'):
                description = supplemental.get('description')
            else:
                description = supplemental.get('schema-intro', description)

            # Profile purpose overrides all:
            if profile:
                description = profile.get('Purpose')

            if description:
                self.add_description(description)

            if len(uris):
                self.add_uris(uris)

            self.add_json_payload(supplemental.get('jsonpayload'))

            if 'properties' in details.keys():
                prop_details = {}
                conditional_details = {}

                properties = details['properties']
                prop_names = [x for x in properties.keys()]
                prop_names = self.organize_prop_names(prop_names, profile)

                for prop_name in prop_names:
                    prop_info = properties[prop_name]

                    prop_info['prop_required'] = prop_name in required
                    prop_info['prop_required_on_create'] = prop_name in required_on_create
                    prop_info['parent_requires'] = required
                    prop_info['parent_requires_on_create'] = required_on_create
                    prop_info['required_parameter'] = prop_info.get('requiredParameter') == True

                    meta = prop_info.get('_doc_generator_meta', {})
                    prop_infos = self.extend_property_info(schema_ref, prop_info, properties.get('_doc_generator_meta'))

                    formatted = self.format_property_row(schema_ref, prop_name, prop_infos, [])
                    if formatted:
                        self.add_property_row(formatted['row'])
                        if formatted['details']:
                            prop_details.update(formatted['details'])
                        if formatted['action_details']:
                            self.add_action_details(formatted['action_details'])
                        if formatted.get('profile_conditional_details'):
                            conditional_details.update(formatted['profile_conditional_details'])

                if len(prop_details):
                    detail_names = [x for x in prop_details.keys()]
                    detail_names.sort(key=str.lower)
                    for detail_name in detail_names:
                        self.add_property_details(prop_details[detail_name])

                if len(conditional_details):
                    cond_names = [x for x in conditional_details.keys()]
                    cond_names.sort(key=str.lower)
                    for cond_name in cond_names:
                        self.add_profile_conditional_details(conditional_details[cond_name])

        if self.config.get('profile_mode'):
            # Add registry messages, if in profile.
            registry_reqs = config.get('profile').get('registries_annotated', {})
            if registry_reqs:
                self.add_registry_reqs(registry_reqs)

        return self.output_document()


    def generate_fragment_doc(self, ref, config):
        """Given a path to a definition, generate a block of documentation.

        Used to generate documentation for schema fragments.
        """

        # If /properties is specified, expand the object and output just its contents.
        if ref.endswith('/properties'):
            ref = ref[:-len('/properties')]
            config['strip_top_object'] = True

        if not ref:
            warnings.warn("Can't generate fragment for '" + ref +
                          "': could not parse as schema URI.")
            return ''

        frag_gen = self.__class__(self.property_data, self.traverser, config, self.level)

        if "://" not in ref:
            # Try to find the file locally
            try:
                filepath = ref.split('#')[0]
                localpath = os.path.abspath(filepath)
                fragment_data = DocGenUtilities.load_as_json(localpath)
                if fragment_data:
                    traverser = self.traverser.copy()
                    traverser.add_schema(filepath, fragment_data)
                    frag_gen = self.__class__(self.property_data, traverser, config, self.level)
            except Exception as ex:
                # That's okay, it may still be a URI-style ref without the protocol
                pass

        prop_info = frag_gen.traverser.find_ref_data(ref)

        # Give frag_gen our common_properties to share. This way, we get the updates.
        frag_gen.common_properties = self.common_properties

        if not prop_info:
            warnings.warn("Can't generate fragment for '" + ref + "': could not find data.")
            return ''

        schema_ref = prop_info['_from_schema_ref']
        prop_name = prop_info['_prop_name']
        meta = prop_info.get('_doc_generator_meta')
        if not meta:
            meta = {}
        prop_infos = frag_gen.extend_property_info(schema_ref, prop_info)

        formatted = frag_gen.format_property_row(schema_ref, prop_name, prop_infos, [])
        if formatted:
            frag_gen.add_section('')
            frag_gen.current_version = {}

            frag_gen.add_property_row(formatted['row'])
            if len(formatted['details']):
                prop_details = {}
                prop_details.update(formatted['details'])
                detail_names = [x for x in prop_details.keys()]
                detail_names.sort(key=str.lower)
                for detail_name in detail_names:
                    frag_gen.add_property_details(prop_details[detail_name])

            if formatted['action_details']:
                frag_gen.add_action_details(formatted['action_details'])

        return frag_gen.emit()


    def generate_common_properties_doc(self):
        """ Generate output for common object properties """
        config = copy.deepcopy(self.config)
        config['strip_top_object'] = True
        schema_supplement = config.get('schema_supplement', {})

        cp_gen = self.__class__(self.property_data, self.traverser, config, self.level)

        # Sort the properties by prop_name
        def sortkey(elt):
            key = elt[1].get('_prop_name', '') + ' ' + elt[1].get('_latest_version', '') +  elt[0]
            return key.lower()
        sorted_properties = sorted(self.common_properties.items(), key=sortkey)

        for prop_tuple in sorted_properties:
            (ref, prop_info) = prop_tuple
            schema_ref = prop_info['_from_schema_ref']
            prop_name = prop_info['_prop_name']

            if self.skip_schema(prop_name):
                continue;
            meta = prop_info.get('_doc_generator_meta')
            version = prop_info.get('_latest_version')
            if not version:
                version = DocGenUtilities.get_ref_version(prop_info.get('_ref_uri', ''))

            if not meta:
                meta = {}

            prop_infos = cp_gen.extend_property_info(schema_ref, prop_info) # TODO: Do we really need to expand this?

            # Get the supplemental details for this property/version.
            # (Probably the version information is not desired?)
            prop_key = prop_name
            if version:
                major_version = version.split('.')[0]
                prop_key = prop_name + '_' + major_version

            supplemental = schema_supplement.get(prop_key,
                                                 schema_supplement.get(prop_name, {}))

            formatted = cp_gen.format_property_row(schema_ref, prop_name, prop_infos, [])
            if formatted:
                # TODO: There is an opportunity here to refactor with code around line 319 in generate_output.
                ref_id = 'common-properties-' + prop_name
                if version:
                    ref_id += '_v' + version
                    # prop_name += ' ' + version

                cp_gen.add_section(prop_name, ref_id)
                cp_gen.add_json_payload(supplemental.get('jsonpayload'))

                # Override with supplemental schema description, if provided
                # If there is a supplemental Description or Schema-Intro, it replaces
                # the description in the schema. If both are present, the Description
                # should be output, followed by the Schema-Intro.
                description = self.get_property_description(prop_info)

                if supplemental.get('description') and supplemental.get('schema-intro'):
                    description = (supplemental.get('description') + '\n\n' +
                                   supplemental.get('schema-intro'))
                elif supplemental.get('description'):
                    description = supplemental.get('description')
                else:
                    description = supplemental.get('schema-intro', description)

                if description:
                    cp_gen.add_description(description)
                cp_gen.current_version = {}

                cp_gen.add_property_row(formatted['row'])
                if len(formatted['details']):
                    prop_details = {}
                    prop_details.update(formatted['details'])
                    detail_names = [x for x in prop_details.keys()]
                    detail_names.sort(key=str.lower)
                    for detail_name in detail_names:
                        cp_gen.add_property_details(prop_details[detail_name])

                if formatted['action_details']:
                    cp_gen.add_action_details(formatted['action_details'])


        return cp_gen.emit()


    def generate_collections_doc(self):
        """ Generate output for collections. This is a table of CollectionName, URIs. """

        collections_uris = self.get_collections_uris()
        if not collections_uris:
            return ''

        doc = ""
        header = self.formatter.make_header_row(['Collection Type', 'URIs'])
        rows = []
        for collection_name, uris in sorted(collections_uris.items(), key=lambda x: x[0].lower()):
            item_text = '<br>'.join([self.format_uri(x) for x in sorted(uris, key=str.lower)])
            rows.append(self.formatter.make_row([collection_name, item_text]))
        doc = self.formatter.make_table(rows, [header], 'uris')
        return doc


    def get_collections_uris(self):
        """ Get just the collection names and URIs from property_data.

        Collections are identified by "Collection" in the normalized_uri. """
        data = {}
        collection_keys = sorted([x for x in self.property_data if 'Collection.' in x], key=str.lower)
        for x in collection_keys:
            [preamble, collection_file_name] = x.rsplit('/', 1)
            [collection_name, rest] = collection_file_name.split('.', 1)
            uris = sorted(self.property_data[x].get('uris', []), key=str.lower)
            data[collection_name] = [self.format_uri(x) for x in uris]
        return data


    def extend_property_info(self, schema_ref, prop_info, context_meta=None):
        """If prop_info contains a $ref or anyOf attribute, extend it with that information.

        Returns an array of objects. Arrays of arrays of objects are possible but not expected.
        """
        traverser = self.traverser
        prop_ref = prop_info.get('$ref', None)
        prop_anyof = prop_info.get('anyOf', None)
        if not context_meta:
            context_meta = {}

        prop_infos = []
        outside_ref = None
        schema_name = traverser.get_schema_name(schema_ref)

        # Check for anyOf with a $ref to odata.4.0.0 idRef, and replace it with that ref.
        if prop_anyof:
            for elt in prop_anyof:
                if '$ref' in elt:
                    this_ref = elt.get('$ref')
                    if this_ref.endswith('#/definitions/idRef'):
                        is_link = True
                        prop_ref = this_ref
                        prop_anyof = None
                        break

        if prop_ref:
            if prop_ref.startswith('#'):
                prop_ref = schema_ref + prop_ref
            else:
                idref_info = self.process_for_idRef(prop_ref)
                if idref_info:
                    prop_ref = None
                    # if parent_props were specified in prop_info, they take precedence:
                    for x in prop_info.keys():
                        if x in self.parent_props and prop_info[x]:
                            idref_info[x] = prop_info[x]
                    prop_info = idref_info

        if prop_ref:
            ref_info = traverser.find_ref_data(prop_ref)

            if not ref_info:
                warnings.warn("Unable to find data for " + prop_ref)

            else:

                prop_meta = prop_info.get('_doc_generator_meta', {})

                # Update version info from the ref, provided that it is within the same schema.
                # Make the comparison by unversioned ref, in respect of the way common_properties are keyed
                from_schema_ref = ref_info.get('_from_schema_ref')
                unversioned_schema_ref = DocGenUtilities.make_unversioned_ref(from_schema_ref)
                is_other_schema = from_schema_ref and not ((schema_ref == from_schema_ref) or (schema_ref == unversioned_schema_ref))

                if not is_other_schema:
                    ref_meta = ref_info.get('_doc_generator_meta', {})
                    meta = self.merge_full_metadata(prop_meta, ref_meta)
                else:
                    meta = prop_meta
                node_name = traverser.get_node_from_ref(prop_ref)
                meta = self.merge_metadata(node_name, meta, context_meta)

                is_documented_schema = self.is_documented_schema(from_schema_ref)
                is_collection_of = traverser.is_collection_of(from_schema_ref)
                prop_name = ref_info.get('_prop_name', False)
                is_ref_to_same_schema = ((not is_other_schema) and prop_name == schema_name)

                if is_collection_of and ref_info.get('anyOf'):
                    anyof_ref = None
                    for a_of in ref_info.get('anyOf'):
                        if '$ref' in a_of:
                            anyof_ref = a_of['$ref']
                            break;
                    if anyof_ref:
                        idref_info = self.process_for_idRef(anyof_ref)
                        if idref_info:
                            ref_info = idref_info
                ref_info = self.apply_overrides(ref_info)

                # If an object, include just the definition and description, and append a reference if possible:
                if ref_info.get('type') == 'object':
                    ref_description = ref_info.get('description')
                    ref_longDescription = ref_info.get('longDescription')
                    ref_fulldescription_override = ref_info.get('fulldescription_override')
                    ref_pattern = ref_info.get('pattern')
                    link_detail = ''
                    append_ref = ''

                    from_schema_uri, _, _ = ref_info.get('_ref_uri', '').partition('#')

                    # Links to other Redfish resources are a special case.
                    if is_other_schema or is_ref_to_same_schema:
                        if is_collection_of:
                            append_ref = 'Contains a link to a resource.'
                            ref_schema_name = self.traverser.get_schema_name(is_collection_of)

                            if 'redfish.dmtf.org/schemas/v1/odata' in from_schema_uri:
                                from_schema_uri = 'http://' + is_collection_of

                            link_detail = ('Link to Collection of ' + self.link_to_own_schema(is_collection_of, from_schema_uri)
                                           + '. See the ' + ref_schema_name + ' schema for details.')

                        else:
                            if is_documented_schema:
                                link_detail = ('Link to a ' + prop_name + ' resource. See the Links section and the '
                                               + self.link_to_own_schema(from_schema_ref, from_schema_uri) +
                                               ' schema for details.')

                            if is_ref_to_same_schema:
                                # e.g., a Chassis is contained by another Chassis
                                link_detail = ('Link to another ' + prop_name + ' resource.')

                            else:
                                wants_common_objects = self.config.get('wants_common_objects')
                                if is_documented_schema or not wants_common_objects:
                                    append_ref = ('See the ' + self.link_to_own_schema(from_schema_ref, from_schema_uri) +
                                                  ' schema for details on this property.')
                                else:
                                    # This looks like a Common Object! We should have an unversioned ref for this.
                                    requested_ref_uri = ref_info['_ref_uri']
                                    ref_key = DocGenUtilities.make_unversioned_ref(ref_info['_ref_uri'])
                                    if ref_key:
                                        parent_info = traverser.find_ref_data(ref_key)
                                        if parent_info:
                                            ref_info = self.apply_overrides(parent_info)
                                    else:
                                        ref_key = ref_info['_ref_uri']

                                    if self.common_properties.get(ref_key) is None:
                                        self.common_properties[ref_key] = ref_info

                                    if not self.skip_schema(ref_info.get('_prop_name')):
                                        specific_version = DocGenUtilities.get_ref_version(requested_ref_uri)
                                        if 'type' not in ref_info:
                                            # This clause papers over a bug; somehow we never get to the bottom
                                            # of IPv6GatewayStaticAddress.
                                            ref_info['type'] = 'object'
                                        if specific_version:
                                            append_ref = ('See the ' + self.link_to_common_property(ref_key) + ' '
                                                          + '(v' + str(specific_version) + ')' +
                                                          ' for details on this property.')
                                        else:
                                            append_ref = ('See the ' + self.link_to_common_property(ref_key) +
                                                          ' for details on this property.')



                        new_ref_info = {
                            'description': ref_description,
                            'longDescription': ref_longDescription,
                            'fulldescription_override': ref_fulldescription_override,
                            'pattern': ref_pattern,
                            }
                        props_to_add = ['_prop_name', '_from_schema_ref', '_schema_name', 'type', 'readonly']
                        for x in props_to_add:
                            if ref_info.get(x):
                                new_ref_info[x] = ref_info[x]

                        if not ref_fulldescription_override:
                            new_ref_info['add_link_text'] = append_ref

                        if link_detail:
                            link_props = {'type': 'string',
                                          'readonly': True,
                                          'description': '',
                                          }
                            if not ref_fulldescription_override:
                                link_props['add_link_text'] = link_detail
                            new_ref_info['properties'] = {'@odata.id': link_props}

                        ref_info = new_ref_info

                # if parent_props were specified in prop_info, they take precedence:
                for x in prop_info.keys():
                    if x in self.parent_props and prop_info[x]:
                        ref_info[x] = prop_info[x]
                prop_info = ref_info

                # override metadata with merged metadata from above.
                prop_info['_doc_generator_meta'] = meta

                if '$ref' in prop_info or 'anyOf' in prop_info:
                    return self.extend_property_info(schema_ref, prop_info, context_meta)

            prop_infos.append(prop_info)

        elif prop_anyof:
            skip_null = len([x for x in prop_anyof if '$ref' in x])
            sans_null = [x for x in prop_anyof if x.get('type') != 'null']
            is_nullable = skip_null and [x for x in prop_anyof if x.get('type') == 'null']

            # This is a special case for references to multiple versions of the same object.
            # Get the most recent version, and make it the prop_ref.
            # The expected result is that these show up as referenced objects.
            if len(sans_null) > 1:
                match_ref = unversioned_ref = ''
                latest_ref = latest_version = ''
                refs_by_version = {}
                for elt in prop_anyof:
                    this_ref = elt.get('$ref')
                    if this_ref:
                        unversioned_ref = DocGenUtilities.make_unversioned_ref(this_ref)
                        this_version = DocGenUtilities.get_ref_version(this_ref)
                        if this_version:
                            cleaned_version = this_version.replace('_', '.')
                            refs_by_version[cleaned_version] = this_ref
                        else:
                            break

                    if not match_ref:
                        match_ref = unversioned_ref
                    if not latest_ref:
                        latest_ref = this_ref
                        latest_version = cleaned_version
                    else:
                        compare = DocGenUtilities.compare_versions(latest_version, cleaned_version)
                        if compare < 0:
                            latest_version = cleaned_version
                    if match_ref != unversioned_ref: # These are not all versions of the same thing
                        break

                if match_ref == unversioned_ref:
                    # Replace the anyof with a ref to the latest version:
                    prop_ref = refs_by_version[latest_version]
                    prop_anyof = [ {
                        '$ref': prop_ref
                        }]

            for elt in prop_anyof:
                if skip_null and (elt.get('type') == 'null'):
                    continue
                if '$ref' in elt:
                    for x in prop_info.keys():
                        if x in self.parent_props:
                            elt[x] = prop_info[x]
                elt = self.extend_property_info(schema_ref, elt, context_meta)
                prop_infos.extend(elt)

            # If this is a nullable property (based on {type: 'null'} object AnyOf), add 'null' to the type.
            if is_nullable:
                prop_infos[0]['nullable'] = True
                if prop_infos[0].get('type'):
                    prop_infos[0]['type'] = [prop_infos[0]['type'], 'null']
                else:
                    prop_infos[0]['type'] = 'null'

        else:
            prop_infos.append(prop_info)

        return prop_infos


    def organize_prop_names(self, prop_names, profile=None):
        """ Strip out excluded property names, sorting the remainder """

        if self.config.get('profile_mode'):
            prop_names = self.filter_props_by_profile(prop_names, profile)
        prop_names = self.exclude_prop_names(prop_names, self.config['excluded_properties'],
                                       self.config['excluded_by_match'])
        prop_names.sort(key=str.lower)
        return prop_names


    def filter_props_by_profile(self, prop_names, profile, is_action=False):
        if profile is None:
            return []

        if self.config.get('profile_mode') == 'terse':
            if is_action:
                profile_props = [x for x in profile.keys()]
            else:
                profile_props = [x for x in profile.get('PropertyRequirements', {}).keys()]
            if profile.get('ActionRequirements'):
                profile_props.append('Actions')

            if is_action:
                # Action properties typically start with "#SchemaName.", which is not reflected in the profile:
                filtered = []
                for prop in profile_props:
                    if prop in prop_names:
                        filtered.append(prop)
                    else:
                        matches = [x for x in prop_names if x.endswith('.' + prop)]
                        if matches:
                            filtered.append(matches[0])
                prop_names = filtered
            else:
                prop_names = list(set(prop_names) & set(profile_props))
        prop_names.sort(key=str.lower)
        return prop_names


    def exclude_annotations(self, prop_names):
        """ Strip out excluded annotations, sorting the remainder """

        return self.exclude_prop_names(prop_names, self.config.get('excluded_annotations', []),
                                       self.config.get('excluded_annotations_by_match', []))


    def exclude_prop_names(self, prop_names, props_to_exclude, props_to_exclude_by_match):
        """Strip out excluded property names, and sort the remainder."""

        # Strip out properties based on exact match:
        prop_names = [x for x in prop_names if x not in props_to_exclude]

        # Strip out properties based on partial match:
        included_prop_names = []
        for prop_name in prop_names:
            excluded = False
            for prop in props_to_exclude_by_match:
                if prop in prop_name:
                    excluded = True
                    break
            if not excluded:
                included_prop_names.append(prop_name)

        included_prop_names.sort(key=str.lower)
        return included_prop_names


    def skip_schema(self, schema_name):
        """ True if this schema should be skipped in the output """

        if self.config.get('profile_mode'):
            if schema_name in self.config.get('profile', {}).get('Resources', {}):
                return False

        if schema_name in self.config.get('excluded_schemas', []):
            return True
        for pattern in self.config.get('excluded_schemas_by_match', []):
            if pattern in schema_name:
                return True
        return False


    def parse_property_info(self, schema_ref, prop_name, prop_infos, prop_path, within_action=False):
        """Parse a list of one more more property info objects into strings for display.

        Returns a dict of 'prop_type', 'read_only', 'descr', 'prop_is_object',
        'prop_is_array', 'object_description', 'prop_details', 'item_description',
        'has_direct_prop_details', 'has_action_details', 'action_details', 'nullable',
        'is_in_profile', 'profile_read_req', 'profile_write_req', 'profile_mincount', 'profile_purpose',
        'profile_conditional_req', 'profile_conditional_details', 'profile_values', 'profile_comparison',
        'pattern', 'prop_required', 'prop_required_on_create', 'required_parameter'
        """
        if isinstance(prop_infos, dict):
            return self._parse_single_property_info(schema_ref, prop_name, prop_infos,
                                                    prop_path, within_action)

        if len(prop_infos) == 1:
            prop_info = prop_infos[0]
            if isinstance(prop_info, dict):
                return self._parse_single_property_info(schema_ref, prop_name, prop_info,
                                                        prop_path, within_action)
            else:
                return self.parse_property_info(schema_ref, prop_name, prop_info, prop_path, within_action)

        parsed = {
                  'prop_type': [],
                  'prop_units': False,
                  'read_only': False,
                  'descr': [],
                  'add_link_text': '',
                  'prop_is_object': False,
                  'prop_is_array': False,
                  'nullable': False,
                  'object_description': [],
                  'item_description': [],
                  'prop_details': {},
                  'has_direct_prop_details': False,
                  'has_action_details': False,
                  'action_details': {},
                  'pattern': [],
                  'prop_required': False,
                  'prop_required_on_create': False,
                  'required_parameter': False,
                  'is_in_profile': False,
                  'profile_read_req': None,
                  'profile_write_req': None,
                  'profile_mincount': None,
                  'profile_purpose': None,
                  'profile_conditional_req': None,
                  'profile_conditional_details': None,
                  'profile_values': None,
                  'profile_comparison': None
                 }

        profile = None
        # Skip profile data if prop_name is blank -- this is just an additional row of info and
        # the "parent" row will have the profile info.
        if self.config.get('profile_mode') and prop_name:
            profile_section = 'PropertyRequirements'
            if within_action:
                profile_section = 'ActionRequirements'
            path_to_prop = prop_path.copy()
            path_to_prop.append(prop_name)
            profile = self.get_prop_profile(schema_ref, path_to_prop, profile_section)

        anyof_details = [self.parse_property_info(schema_ref, prop_name, x, prop_path, within_action)
                         for x in prop_infos]

        # Remove details for anyOf props with prop_type = 'null'.
        details = []
        has_null = False
        for det in anyof_details:
            if len(det['prop_type']) == 1 and 'null' in det['prop_type']:
                has_null = True
            else:
                details.append(det)
        # Save the null flag so we can display it later:
        parsed['nullable'] = has_null


        # Uniquify these properties and save as lists:
        props_to_combine = ['prop_type', 'descr', 'object_description', 'item_description', 'pattern']

        for property_name in props_to_combine:
            property_values = []
            for det in anyof_details:
                if isinstance(det[property_name], list):
                    for val in det[property_name]:
                        if val and val not in property_values:
                            property_values.append(val)
                else:
                    val = det[property_name]
                    if val and val not in property_values:
                        property_values.append(val)
            parsed[property_name] = property_values

        # Restore the pattern to a single string:
        parsed['pattern'] = '\n'.join(parsed['pattern'])

        # read_only and units should be the same for all
        parsed['read_only'] = details[0]['read_only']
        parsed['prop_units'] = details[0]['prop_units']
        parsed['prop_required'] = details[0]['prop_required']
        parsed['prop_required_on_create'] = details[0]['prop_required_on_create']
        parsed['required_parameter'] = details[0].get('requiredParameter') == True

        # Data from profile:
        if profile is not None:
            parsed['is_in_profile'] = True
            parsed['profile_read_req'] = profile.get('ReadRequirement', 'Mandatory')
            parsed['profile_write_req'] = profile.get('WriteRequirement')
            parsed['profile_mincount'] = profile.get('MinCount')
            parsed['profile_purpose'] = profile.get('Purpose')
            parsed['profile_conditional_req'] = profile.get('ConditionalRequirements')
            profile_values = profile.get('Values')
            if profile_values:
                profile_comparison = profile.get('Comparison', 'AnyOf') # Default if Comparison absent
                parsed['profile_values'] = profile_values
                parsed['profile_comparison'] = profile_comparison

            for det in details:
                parsed['prop_is_object'] |= det['prop_is_object']
                parsed['prop_is_array'] |= det['prop_is_array']
                parsed['has_direct_prop_details'] |= det['has_direct_prop_details']
                parsed['prop_details'].update(det['prop_details'])
                parsed['has_action_details'] |= det['has_action_details']
                parsed['action_details'].update(det['action_details'])
                parsed['profile_conditional_details'].update(det['profile_conditional_details'])

        return parsed


    def _parse_single_property_info(self, schema_ref, prop_name, prop_info, prop_path, within_action=False):
        """Parse definition of a specific property into strings for display.

        Returns a dict of 'prop_type', 'prop_units', 'read_only', 'descr', 'add_link_text',
        'prop_is_object', 'prop_is_array', 'object_description', 'prop_details', 'item_description',
        'has_direct_prop_details', 'has_action_details', 'action_details', 'nullable',
        'is_in_profile', 'profile_read_req', 'profile_write_req', 'profile_mincount', 'profile_purpose',
        'profile_conditional_req', 'profile_conditional_details', 'profile_values', 'profile_comparison',
        'normative_descr', 'non_normative_descr', 'pattern', 'prop_required', 'prop_required_on_create',
        'required_parameter'
        """
        traverser = self.traverser

        # type may be a string or a list.
        prop_details = {}
        prop_type = prop_info.get('type', [])
        prop_is_object = False
        object_description = ''
        prop_is_array = False
        item_description = ''
        item_list = '' # For lists of simple types
        array_of_objects = False
        has_prop_details = False
        has_prop_actions = False
        action_details = {}
        profile_conditional_req = False
        profile_conditional_details = {}
        profile_values = False
        profile_comparison = False
        schema_name = traverser.get_schema_name(schema_ref)

        # Get the profile if we are in profile mode.
        # Skip profile data if prop_name is blank -- this is just an additional row of info and
        # the "parent" row will have the profile info.
        profile = None
        if self.config.get('profile_mode') and prop_name:
            prop_brief_name = prop_name
            profile_section = 'PropertyRequirements'
            if within_action:
                profile_section = 'ActionRequirements'
                if prop_name.startswith('#'): # expected
                    prop_name_parts = prop_name.split('.')
                    prop_brief_name = prop_name_parts[-1]
            path_to_prop = prop_path.copy()
            path_to_prop.append(prop_brief_name)
            profile = self.get_prop_profile(schema_ref, path_to_prop, profile_section)

        # Some special treatment is required for Actions
        is_action = prop_name == 'Actions'
        if within_action:
            has_prop_actions = True

        # Only objects within Actions have parameters
        action_parameters = prop_info.get('parameters', {})

        prop_info = self.apply_overrides(prop_info)

        if isinstance(prop_type, list):
            prop_is_object = 'object' in prop_type
            prop_is_array = 'array' in prop_type
        else:
            prop_is_object = prop_type == 'object'
            prop_is_array = prop_type == 'array'
            prop_type = [prop_type]

        cleaned_prop_type = []
        has_null = False
        for pt in prop_type:
            if pt == 'null':
                has_null = True
            else:
                cleaned_prop_type.append(pt)
        prop_type = cleaned_prop_type

        prop_units = prop_info.get('units')

        read_only = prop_info.get('readonly')

        prop_required = prop_info.get('prop_required')
        prop_required_on_create = prop_info.get('prop_required_on_create')
        required_parameter = prop_info.get('requiredParameter')

        descr = self.get_property_description(prop_info)
        fulldescription_override = prop_info.get('fulldescription_override')

        required = prop_info.get('required', [])
        required_on_create = prop_info.get('requiredOnCreate', [])


        add_link_text = prop_info.get('add_link_text', '')

        if within_action:
            # Extend and parse parameter info
            for action_param in action_parameters.keys():
                params = action_parameters[action_param]
                params = self.extend_property_info(schema_ref, params, {})
                action_parameters[action_param] = self.extend_property_info(schema_ref, action_parameters[action_param], {})

            action_details = self.format_action_parameters(schema_ref, prop_name, descr, action_parameters)

            formatted_action_rows = []

            for param_name in action_parameters:
                if prop_name.startswith('#'):
                    [skip, action_name] = prop_name.rsplit('.', 1)
                else:
                    action_name = prop_name

                new_path = prop_path.copy()
                new_path.append(action_name)
                formatted_action = self.format_property_row(schema_ref, param_name, action_parameters[param_name], new_path)

                # Capture the enum details and merge them into the ones for the overall properties:
                if formatted_action.get('details'):
                    has_prop_details = True
                    prop_details.update(formatted_action['details'])

            self.add_action_details(action_details)


        # Items, if present, will have a definition with either an object, a list of types,
        # or a $ref:
        prop_item = prop_info.get('items')
        list_of_objects = False
        list_of_simple_type = False # For references to simple types
        collapse_description = False
        promote_me = False # Special case to replace enclosing array with combined array/simple-type

        if isinstance(prop_item, dict):
            if 'type' in prop_item and 'properties' not in prop_item:
                prop_items = [prop_item]
                collapse_description = True
            else:
                prop_items = self.extend_property_info(schema_ref, prop_item, prop_info.get('_doc_generator_meta'))
                array_of_objects = True

                if len(prop_items) == 1:
                    if 'type' in prop_items[0] and 'properties' not in prop_items[0]:
                        list_of_simple_type = True

            list_of_objects = not list_of_simple_type

        # Enumerations go into Property Details
        prop_enum = prop_info.get('enum')
        supplemental_details = None

        if 'supplemental' in self.config and 'property details' in self.config['supplemental']:
            detconfig = self.config['supplemental']['property details']
            if schema_name in detconfig and prop_name in detconfig[schema_name]:
                supplemental_details = detconfig[schema_name][prop_name]

        if prop_enum or supplemental_details:
            has_prop_details = True

            if self.config.get('normative') and 'enumLongDescriptions' in prop_info:
                prop_enum_details = prop_info.get('enumLongDescriptions')
            else:
                prop_enum_details = prop_info.get('enumDescriptions')
            anchor = schema_ref + '|details|' + prop_name
            prop_details[prop_name] = self.format_property_details(prop_name, prop_type, descr,
                                                                   prop_enum, prop_enum_details,
                                                                   supplemental_details,
                                                                   prop_info.get('_doc_generator_meta', {}),
                                                                   anchor, profile)

        # Action details may be supplied as markdown in the supplemental doc.
        # Possibly we should be phasing this out.
        supplemental_actions = None
        if 'supplemental' in self.config and 'action details' in self.config['supplemental']:
            action_config = self.config['supplemental']['action details']
            action_name = prop_name
            if '.' in action_name:
                _, _, action_name = action_name.rpartition('.')
            if action_config.get(schema_name) and action_name in action_config[schema_name].keys():
                supplemental_actions = action_config[schema_name][action_name]
                supplemental_actions['action_name'] = action_name

        if supplemental_actions:
            has_prop_actions = True
            formatted_actions = self.format_action_details(prop_name, supplemental_actions)
            action_details = supplemental_actions
            self.add_action_details(formatted_actions)

        # embedded object:
        if prop_is_object:
            new_path = prop_path.copy()
            new_path.append(prop_name)

            prop_info['parent_requires'] = required
            prop_info['parent_requires_on_create'] = required_on_create

            object_formatted = self.format_object_descr(schema_ref, prop_info, new_path, is_action)
            object_description = object_formatted['rows']
            if object_formatted['details']:
                prop_details.update(object_formatted['details'])

        # embedded items:
        if prop_is_array:
            new_path = prop_path.copy()
            new_path.append(prop_name)
            if list_of_objects:
                item_formatted = self.format_list_of_object_descrs(schema_ref, prop_items, new_path)
                if collapse_description:
                    # remember, we set collapse_description when we made prop_items a single-element list.
                    item_list = prop_items[0].get('type')

            elif list_of_simple_type:
                if self.collapse_list_of_simple_type:
                    # We want to combine the array and its item(s) into a single row. Create a combined
                    # prop_item to make it work.
                    combined_prop_item = prop_items[0]
                    combined_prop_item['_prop_name'] = prop_name
                    combined_prop_item['readonly'] = prop_info.get('readonly', False)
                    if self.config.get('normative') and 'longDescription' in combined_prop_item:
                        descr = descr + ' ' + combined_prop_item['longDescription']
                        combined_prop_item['longDescription'] = descr
                    else:
                        if prop_items[0].get('description'):
                            descr = descr + ' ' + combined_prop_item['description']
                        combined_prop_item['description'] = descr
                    if fulldescription_override:
                        combined_prop_item['fulldescription_override'] = fulldescription_override

                    item_formatted = self.format_non_object_descr(schema_ref, combined_prop_item, new_path, True)

                else:
                    item_formatted = self.format_non_object_descr(schema_ref, prop_items[0], new_path)
                    item_formatted['promote_me'] = False

            else:
                item_formatted = self.format_non_object_descr(schema_ref, prop_item, new_path)

            promote_me = item_formatted.get('promote_me', False)
            item_description = item_formatted['rows']
            if item_formatted['details']:
                prop_details.update(item_formatted['details'])


        # Read/Write requirements from profile:
        if self.config.get('profile_mode') and prop_name and profile is not None:

            # Conditional Requirements
            profile_conditional_req = profile.get('ConditionalRequirements')
            if profile_conditional_req:
                # Add the read and write reqs, as we want to capture those as "Base Requirement":
                req = {'BaseRequirement': True}
                req['ReadRequirement'] = profile.get('ReadRequirement')
                req['WriteRequirement'] = profile.get('WriteRequirement')
                profile_conditional_req.insert(0, req)
                profile_conditional_details[prop_name] = self.format_conditional_details(schema_ref, prop_name,
                                                                                         profile_conditional_req)
            # Comparison
            profile_values = profile.get('Values')
            if profile_values:
                profile_comparison = profile.get('Comparison', 'AnyOf') # Default if Comparison absent


        parsed_info = {'_prop_name': prop_name,
                       'prop_type': prop_type,
                       'prop_units': prop_units,
                       'read_only': read_only,
                       'nullable': has_null,
                       'descr': descr,
                       'add_link_text': add_link_text,
                       'prop_is_object': prop_is_object,
                       'prop_is_array': prop_is_array,
                       'array_of_objects': array_of_objects,
                       'object_description': object_description,
                       'item_description': item_description,
                       'item_list': item_list,
                       'prop_details': prop_details,
                       'has_direct_prop_details': has_prop_details,
                       'has_action_details': has_prop_actions,
                       'action_details': action_details,
                       'promote_me': promote_me,
                       'normative_descr': prop_info.get('longDescription', ''),
                       'non_normative_descr': prop_info.get('description', ''),
                       'fulldescription_override': prop_info.get('fulldescription_override', False),
                       'prop_required': prop_required,
                       'prop_required_on_create': prop_required_on_create,
                       'required_parameter': required_parameter,
                       'pattern': prop_info.get('pattern'),
                       'is_in_profile': False,
                       'profile_read_req': None,
                       'profile_write_req': None,
                       'profile_mincount': None,
                       'profile_purpose': None,
                       'profile_conditional_req': None,
                       'profile_conditional_details': None,
                       'profile_values': None,
                       'profile_comparison': None
                       }

        if profile is not None:
            parsed_info.update({
                'is_in_profile': True,
                'profile_read_req': profile.get('ReadRequirement', 'Mandatory'),
                'profile_write_req': profile.get('WriteRequirement'),
                'profile_mincount': profile.get('MinCount'),
                'profile_purpose': profile.get('Purpose'),
                'profile_conditional_req': profile_conditional_req,
                'profile_conditional_details': profile_conditional_details,
                'profile_values': profile_values,
                'profile_comparison': profile_comparison,
                })

        return parsed_info


    def process_for_idRef(self, ref):
        """Convenience method to check ref for 'odata.4.0.0#/definitions/idRef' and if so, return its property info.

        We special-case this a couple of places where we treat other refs a little differently. """
        prop_info = None
        if ref.endswith('#/definitions/idRef'):
            # idRef is a special case; we just want to pull in its definition and stop.
            prop_info = self.traverser.find_ref_data(ref)
            if not prop_info:
                # We must not have the odata schema, but we know what it is.
                prop_info = {'properties':
                             {'@odata.id':
                              {'type': 'string',
                               'readonly': True,
                               "description": "The unique identifier for a resource.",
                               "longDescription": "The value of this property shall be the unique identifier for the resource and it shall be of the form defined in the Redfish specification.",
                               }
                              }
                             }
        return prop_info


    def get_property_description(self, prop_info):
        """ Get the right description to output, based on prop data and config """
        descr = None
        if self.config.get('profile_mode') != 'terse':
            if self.config.get('normative') and 'longDescription' in prop_info:
                descr = prop_info.get('longDescription', '')
            else:
                descr = prop_info.get('description', '')

        normative_descr = prop_info.get('longDescription', '')
        non_normative_descr = prop_info.get('description', '')
        pattern = prop_info.get('pattern')

        if self.config.get('normative') and normative_descr:
            descr = normative_descr
        else:
            descr = non_normative_descr

        if self.config.get('normative') and pattern:
            descr = descr + ' Pattern: ' + pattern

        return descr


    def format_object_descr(self, schema_ref, prop_info, prop_path=[], is_action=False):
        """Format the properties for an embedded object."""

        properties = prop_info.get('properties')
        output = []
        details = {}
        action_details = {}
        conditional_details = {}

        context_meta = prop_info.get('_doc_generator_meta')
        if not context_meta:
            context_meta = {}

        # If prop_info was extracted from a different schema, it will be present as
        # _from_schema_ref
        schema_ref = prop_info.get('_from_schema_ref', schema_ref)
        schema_name = self.traverser.get_schema_name(schema_ref)

        required = prop_info.get('required', [])
        required_on_create = prop_info.get('requiredOnCreate', [])

        parent_requires = prop_info.get('parent_requires', [])
        parent_requires_on_create = prop_info.get('parent_requires_on_create', [])

        if properties:
            prop_names = [x for x in properties.keys()]
            prop_names = self.exclude_annotations(prop_names)

            if self.config.get('profile_mode') == 'terse':
                if len(prop_path) and prop_path[0] == 'Actions':
                    profile_section = 'ActionRequirements'
                else:
                    profile_section = 'PropertyRequirements'
                profile = self.get_prop_profile(schema_ref, prop_path, profile_section)

                prop_names = self.filter_props_by_profile(prop_names, profile, is_action)
                filtered_properties = {}
                for k in prop_names:
                    filtered_properties[k] = properties[k]
                prop_info['properties'] = properties = filtered_properties


            if is_action:
                prop_names = [x for x in prop_names if x.startswith('#')]

            for prop_name in prop_names:
                base_detail_info = properties[prop_name]
                base_detail_info['prop_required'] = prop_name in parent_requires
                base_detail_info['prop_required_on_create'] = prop_name in parent_requires_on_create
                base_detail_info = self.apply_overrides(base_detail_info, schema_name, prop_name)
                meta = self.merge_metadata(prop_name, base_detail_info.get('_doc_generator_meta', {}), context_meta)
                detail_info = self.extend_property_info(schema_ref, base_detail_info, meta)
                meta = self.merge_full_metadata(detail_info[0].get('_doc_generator_meta', {}), meta)

                if is_action:
                    # Trim out the properties; these are always Target and Title:
                    detail_info[0]['properties'] = {}

                meta['within_action'] = is_action
                detail_info[0]['_doc_generator_meta'] = meta

                new_path = prop_path.copy()

                formatted = self.format_property_row(schema_ref, prop_name, detail_info, new_path)
                if formatted:
                    output.append(formatted['row'])
                    if formatted['details']:
                        details.update(formatted['details'])
                    if formatted['action_details']:
                        action_details[prop_name] = formatted['action_details']
                    if formatted.get('profile_conditional_details'):
                        conditional_details.update(formatted['profile_conditional_details'])

        if len(conditional_details):
            cond_names = [x for x in conditional_details.keys()]
            cond_names.sort(key=str.lower)
            for cond_name in cond_names:
                self.add_profile_conditional_details(conditional_details[cond_name])



        return {'rows': output, 'details': details, 'action_details': action_details }


    def format_non_object_descr(self, schema_ref, prop_dict, prop_path=[], in_array=False):
        """For definitions that just list simple types without a 'properties' entry"""

        output = []
        details = {}
        action_details = {}

        prop_name = prop_dict.get('_prop_name', '')
        detail_info = self.extend_property_info(schema_ref, prop_dict)

        formatted = self.format_property_row(schema_ref, prop_name, detail_info, prop_path, in_array)

        if formatted:
            output.append(formatted['row'])
            details = formatted.get('details', {})
            action_details = formatted.get('action_details', {})

        return {'rows': output, 'details': details, 'action_details': action_details, 'promote_me': True}


    def link_to_own_schema(self, schema_ref, schema_full_uri):
        """ String for output. Override in HTML formatter to get actual links. """
        schema_name = self.traverser.get_schema_name(schema_ref)
        if schema_name:
            return schema_name
        return schema_ref

    def link_to_common_property(self, ref_key):
        """ String for output. Override in HTML formatter to get actual links. """
        ref_info = self.common_properties.get(ref_key)
        if ref_info and ref_info.get('_prop_name'):
            return ref_info.get('_prop_name') + ' object'
        return ref_key

    def link_to_outside_schema(self, schema_full_uri):
        """ String for output. Override in HTML formatter to get actual links."""
        return schema_full_uri

    def get_documentation_uri(self, ref_uri):
        """ If ref_uri is matched in self.config['uri_replacements'], provide a reference to that """

        if not self.uri_match_keys:
            return None

        replacement = None
        for key in self.uri_match_keys:
            if key in ref_uri:
                match_list = self.config['uri_replacements'][key]
                for match_spec in match_list:
                    if match_spec.get('full_match') and match_spec['full_match'] == ref_uri:
                        replacement = match_spec.get('replace_with')
                    elif match_spec.get('wild_match'):
                        pattern = '.*' + ''.join(match_spec['wild_match']) + '.*'
                        if re.search(pattern, ref_uri):
                            replacement = match_spec.get('replace_with')

        return replacement


    # Override in HTML formatter to get actual links.
    def get_documentation_link(self, ref_uri):
        """ Provide a string referring to ref_uri. """
        target = self.get_documentation_uri(ref_uri)
        if target:
            return "See " + target
        return False

    def is_documented_schema(self, schema_ref):
        """ True if the schema will appear as a section in the output documentation """
        return schema_ref in self.documented_schemas


    def get_ref_for_documented_schema_name(self, schema_name):
        """ Get the schema_ref for the schema_name, if it is a documented schema. """
        candidates = [x for x in self.documented_schemas if schema_name in x]
        for x in candidates:
            if self.property_data[x]['schema_name'] == schema_name:
                return x
        return False


    def apply_overrides(self, prop_info, schema_name=None, prop_name=None):
        """ Apply overrides from config to prop_info. Returns a modified copy of prop_info. """

        prop_info = copy.deepcopy(prop_info)

        if not schema_name:
            schema_name = prop_info.get('_schema_name')

        if not prop_name:
            prop_name = prop_info.get('_prop_name')

        local_overrides = self.config.get('schema_supplement', {}).get(schema_name, {}).get('description overrides', {})
        local_full_overrides = self.config.get('schema_supplement', {}).get(schema_name, {}).get('fulldescription overrides', {})
        prop_info['fulldescription_override'] = False

        if (prop_name in local_overrides) or (prop_name in local_full_overrides):
            if prop_name in local_overrides:
                prop_info['description'] = prop_info['longDescription'] = local_overrides[prop_name]
            if prop_name in local_full_overrides:
                prop_info['description'] = prop_info['longDescription'] = local_full_overrides[prop_name]
                prop_info['fulldescription_override'] = True
            return prop_info
        if prop_name in self.config.get('property_description_overrides', {}):
            prop_info['description'] = prop_info['longDescription'] = self.config['property_description_overrides'][prop_name]
        if prop_name in self.config.get('property_fulldescription_overrides', {}):
            prop_info['description'] = prop_info['longDescription'] = self.config['property_fulldescription_overrides'][prop_name]
            prop_info['fulldescription_override'] = True

        units_trans = self.config.get('units_translation', {}).get(prop_info.get('units'))
        if units_trans:
            prop_info['units'] = units_trans

        return prop_info


    def merge_metadata(self, node_name, meta, context_meta):
        """ Merge version and version_deprecated information from meta with that from context_meta

        context_meta contains version info for the parent, plus embedded version info for node_name
        (and its siblings). We want:
        * (MAYBE) If meta['node_name'] and context_meta['node_name'] both exist, use the older version. For example,
          this can occur when an object was initially defined inline and later moved to the definitions section
          of a schema and included by reference. Presumably definitions could move in the other direction as well!
          We want the version of the first appearance of this property in the schema.
        * If context_meta['node_name']['version'] is newer than meta['version'], use the newer version.
          (implication is that this property was added to the parent after it was already defined elsewhere.)
        For deprecations, it's even less likely differing versions will make sense, but we generally want the
        older version.
        """
        node_meta = context_meta.get(node_name, {})
        meta = self.merge_full_metadata(meta, node_meta)

        return meta


    def merge_full_metadata(self, meta_a, meta_b):
        """ Recursively merge two metadata structures.
        We want to capture the earlier of version and version_deprecated values for all nodes. """

        meta1 = copy.deepcopy(meta_a)
        meta2 = copy.deepcopy(meta_b)

        if ('version' in meta1) and ('version' in meta2):
            compare = DocGenUtilities.compare_versions(meta1['version'], meta2['version'])
            # We want the "first seen" entry, so use the older one.
            if compare > 0:
                meta1['version'] = meta2['version']
        elif 'version' in meta2:
            meta1['version'] = meta2['version']

        # If any of this data is from the unversioned schema, that wins (expected is that it will be from meta2):
        if meta1.get('unversioned'):
            if 'version_deprecated' in meta2:
                del(meta2['version_deprecated'])
            # It's still possible for an unversioned schema to include a deprecation notice!
            meta2['version_deprecated_explanation'] = meta1.get('version_deprecated_explanation', '')
        elif meta2.get('unversioned'):
            if 'version_deprecated' in meta1:
                del(meta1['version_deprecated'])
            # It's still possible for an unversioned schema to include a deprecation notice!
            meta1['version_deprecated_explanation'] = meta2.get('version_deprecated_explanation', '')

        elif ('version_deprecated' in meta1) and ('version_deprecated' in meta2):
            compare = DocGenUtilities.compare_versions(meta1['version_deprecated'], meta2['version_deprecated'])
            if compare > 0:
                # meta2 is older, use that:
                meta1['version_deprecated'] = meta2['version_deprecated']
        elif 'version_deprecated' in meta2:
            meta1['version_deprecated'] = meta2['version_deprecated']
            meta1['version_deprecated_explanation'] = meta2.get('version_deprecated_explanation', '')

        for key, val in meta1.items():
            if isinstance(val, dict):
                if meta2.get(key):
                    meta1[key] = self.merge_full_metadata(meta1[key], meta2[key])
        for key, val in meta2.items():
            if isinstance(val, dict):
                # Just pick up the missed items.
                if not meta1.get(key):
                    meta1[key] = meta2[key]
        return meta1


    def get_prop_profile(self, schema_ref, prop_path, section):
        """Get profile data for the specified property, by schema_ref, prop name path, and section.

        Section is 'PropertyRequirements' or 'ActionRequirements'.
        Returns None if no data is present ({} is a valid data-present result)."""

        prop_profile = None
        if prop_path[0] == 'Actions':
            section = 'ActionRequirements'

        if self.config.get('profile_resources'):
            prop_profile = self.config['profile_resources'].get(schema_ref, None)
            if prop_profile is None:
                return None

            if section == 'ActionRequirements':
                if prop_path[0] == 'Actions':
                    prop_path = prop_path[1:]

            prop_reqs = prop_profile.get(section, None)
            if prop_reqs == None:
                return None
            prop_profile = prop_reqs

            for prop_name in prop_path:
                if not prop_name:
                    continue
                prop_profile = prop_reqs.get(prop_name, None)
                if prop_profile is None:
                    return None
                prop_reqs = prop_profile.get('PropertyRequirements', prop_profile.get('Parameters', {}))

        return prop_profile


    @staticmethod
    def truncate_version(version_string, num_parts):
        """Truncate the version string to at least the specified number of parts.

        Maintains additional part(s) if non-zero.
        """

        parts = version_string.split('.')
        keep = []
        for part in parts:
            if len(keep) < num_parts:
                keep.append(part)
            elif part != '0':
                keep.append(part)
            else:
                break

        return '.'.join(keep)


    @staticmethod
    def text_map(text):
        """Replace string for output -- used to replace strings with nicer English text"""

        output_map = {
            'IfImplemented': 'If Implemented',
            'Conditional': 'Conditional Requirements',
            }
        return output_map.get(text, text)
