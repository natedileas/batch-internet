# -*- coding: utf-8 -*-
"""
pywebcopy.parsers
~~~~~~~~~~~~~~~~~

Parsing of the html and Element generation factory.

"""

__all__ = ['Parser', 'MultiParser']

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup
from bs4.dammit import UnicodeDammit

from pyquery import PyQuery
from w3lib.encoding import html_to_unicode
from parse import findall, search as parse_search

from lxml.etree import Comment, HTMLParser
from lxml.html import parse as lxml_parse, fromstring, tostring
from lxml.html.clean import Cleaner
# noinspection PyProtectedMember
from lxml.html import (
    _unquote_match, _archive_re, _nons, _iter_css_imports,
    _iter_css_urls, _parse_meta_refresh_url
)

from six.moves.urllib.parse import urljoin
from .exceptions import ParseError
from .globals import SINGLE_LINK_ATTRIBS, LIST_LINK_ATTRIBS, MARK, __version__

utc_now = datetime.utcnow
# _iter_srcset_urls = re.compile(r'((?:https?://)?[^\s,]+)[\s]+').finditer
# _iter_srcset_urls = re.compile(r'((?:https?://)?(?:[\w\\/.]+))(?:.*?,)?').finditer
# _iter_srcset_urls = re.compile(r"((?:[^\s,]+))(?:.*?,)?").finditer
_iter_srcset_urls = re.compile(r"([^\s,]{5,})", re.MULTILINE).finditer

LOGGER = logging.getLogger('parsers')


class Parser(object):
    """
    Base Parser which builds tree and generates file elements
    and also handles these file elements.

    Built upon the lxml library power.
    """
    __slots__ = '_stack', 'root', '_tree', 'encoding', '_source', \
                '_parseComplete', '_parseBroken'

    def __init__(self):
        self.root = None
        self._tree = None
        self.encoding = 'iso-8859-1'
        self._source = None
        self._stack = list()
        self._parseComplete = False
        self._parseBroken = False

    def __iter__(self):
        if self.root is None:
            self.parse()
        return self._stack.__iter__()

    __next__ = __iter__

    def __len__(self):
        return len(self._stack)

    @property
    def elements(self):
        """
        List of all the elements generated by the parser.
        It can check and invoke the parsing itself if not done in prior.

        :returns: List of Elements
        """
        if self._parseComplete and self.root is None:
            raise RuntimeError("Synchronising error between parse flag and actual parsing.")

        if not self._parseComplete:
            self.parse()
        # if still no parsing
        if self.root is None:
            raise ParseError("Tree not parsed yet!")
        return self._stack

    def get_source(self):
        """
        Returns the resources set for this object.
        This method can be overridden to provide alternate way of source loading.
        """
        if not self._source or not hasattr(self._source, 'read'):
            raise ParseError("Source is not defined or doesn't have a read method!")
        return self._source

    def set_source(self, source, encoding=None):
        """Sets up the resource for this object.

        Use a file like object as source which has a `.read` method or
        you can put in file path if you like.

        :param source: file_like_object or file path
        :param encoding: source encoding
        """
        if isinstance(source, str) and len(source) < 256:
            try:
                source = open(source, 'rb', encoding=encoding)
            except OSError:
                pass
        if not hasattr(source, 'read'):
            raise ParseError(
                "Provided source neither have a read method "
                "nor is a file path."
                "Provide a file like object with `read` method!"
                "or provide a correct file name."
            )
        self._source = source
        self.encoding = encoding

    def _get_utx(self):
        return getattr(self, 'utx', None)

    def parse(self, parser=None, base_url=None):
        """Parses the underlying html source using `lxml` library.

        This parsed tree is stored in :attr:`root` of this object.
        which could be used to perform numerous operations.

        Returns
        -------
            ElementTree
        """
        utx = self._get_utx()

        assert utx is not None, "UrlTransformer not Implemented."  # internal error
        assert utx.base_path is not None, "Base Path is not set!"
        assert utx.base_url is not None, "Base url is not Set!"
        if not isinstance(parser, HTMLParser):
            TypeError("Expected instance of <%r>, got <%r>" % (HTMLParser, parser))

        if not parser:
            parser = HTMLParser(encoding=self.encoding, collect_ids=False)

        source = self.get_source()

        assert source is not None, "Source is not Set!"
        assert hasattr(source, 'read'), "File like object is required!"
        # assert self._element_factory is not None
        # assert hasattr(self._element_factory, 'make_element')
        LOGGER.info(
            'Parsing tree with source: <%r> encoding <%s> and parser <%r>'
            % (self._source, self.encoding, parser)
        )

        context_tree = lxml_parse(source, parser=parser, base_url=base_url)
        # The tree generated by the parse is stored in the self.root
        # variable and can be utilised further for any number of use cases
        self._tree = context_tree
        self.root = context_tree.getroot()

        # ndileas, 10/22/2020: removed watermarking
        # if self.root is not None:
        #     # WaterMarking :)
        #     self.root.insert(0, Comment(MARK.format('', __version__, utx.url, utc_now(), '')))

        # There are internal links present on the html page which are files
        # that includes `#` and `javascript:` and 'data:base64;` type links
        # or a simple `/` url referring anchor tag
        # thus these links needs to be left as is.
        factory = getattr(self, 'make_element', None)
        assert callable(factory), "Element generator is not callable!"

        # Modify the tree elements
        for el in context_tree.iter():
            # A element can contain multiple urls
            for pack in self._handle_lxml_elem(el):

                if pack is not None:
                    elem, attr, url, pos = pack
                else:  # pragma: no cover
                    continue

                if elem is not None:
                    o = factory(elem, attr, url, pos)
                    if o is not None:
                        self._stack.append(o)

        self._parseComplete = True
        return self.root

    @staticmethod
    def _handle_lxml_elem(el):
        """
        From source code of `lxml.html.iter_links` function.
        With added refactoring of multi-urls attributes, i.e. src-set

        Yielding and internally handling (element, attribute, link, pos),
        where attribute may be None
        (indicating the link is in the text).  ``pos`` is the position
        where the link occurs; often 0, but sometimes something else in
        the case of links in stylesheets or style tags.

        Note: multiple links inside of a single text string or
        attribute value are returned in reversed order.  This makes it
        possible to replace or delete them from the text string value
        based on their reported text positions.  Otherwise, a
        modification at one text position can change the positions of
        links reported later on.
        """
        attribs = el.attrib
        tag = _nons(el.tag)
        if tag == 'object':  # pragma: no cover
            codebase = None
            if 'codebase' in attribs:
                codebase = el.get('codebase')
                yield el, 'codebase', codebase, 0
            for attrib in ('classid', 'data'):
                if attrib in attribs:
                    value = el.get(attrib)
                    if codebase is not None:
                        value = urljoin(codebase, value)
                    yield el, attrib, value, 0
            if 'archive' in attribs:
                for match in _archive_re.finditer(el.get('archive')):
                    value = match.group(0)
                    if codebase is not None:
                        value = urljoin(codebase, value)
                    yield el, 'archive', value, match.start()
        else:
            for attrib in SINGLE_LINK_ATTRIBS:
                if attrib in attribs:
                    yield el, attrib, attribs[attrib], 0

            # XXX Patch for multi-url detection
            for attrib in LIST_LINK_ATTRIBS:
                if attrib in attribs:
                    urls = list(_iter_srcset_urls(attribs[attrib]))
                    if urls:
                        # yield in reversed order to simplify in-place modifications
                        for match in urls[::-1]:
                            url, start = _unquote_match(match.group(1).strip(), match.start(1))
                            yield el, attrib, url, start
        if tag == 'meta':
            http_equiv = attribs.get('http-equiv', '').lower()
            if http_equiv == 'refresh':
                content = attribs.get('content', '')
                match = _parse_meta_refresh_url(content)
                url = (match.group('url') if match else content).strip()
                # unexpected content means the redirect won't work, but we might
                # as well be permissive and yield the entire string.
                if url:
                    url, pos = _unquote_match(
                        url, match.start('url') if match else content.find(url))
                    yield el, 'content', url, pos
        elif tag == 'param':
            valuetype = el.get('valuetype') or ''
            if valuetype.lower() == 'ref':
                yield el, 'value', el.get('value'), 0
        elif tag == 'style' and el.text:
            urls = [
                       # (start_pos, url)
                       _unquote_match(match.group(1), match.start(1))[::-1]
                       for match in _iter_css_urls(el.text)
                   ] + [
                       (match.start(1), match.group(1))
                       for match in _iter_css_imports(el.text)
                   ]
            if urls:
                # sort by start pos to bring both match sets back into order
                # and reverse the list to report correct positions despite
                # modifications
                urls.sort(reverse=True)
                for start, url in urls:
                    yield el, None, url, start
        if 'style' in attribs:
            urls = list(_iter_css_urls(attribs['style']))
            if urls:
                # yield in reversed order to simplify in-place modifications
                for match in urls[::-1]:
                    url, start = _unquote_match(match.group(1), match.start(1))
                    yield el, 'style', url, start


# HTML style and script tags cleaner
cleaner = Cleaner()
cleaner.javascript = True
cleaner.style = True


class MultiParser(object):  # pragma: no cover
    """Provides apis specific to scraping or data searching purposes.

    This contains the apis from the requests-html module.

    Most of the source code is from the MIT Licensed library called
    `requests-html` courtesy of kenneth, some code has been heavily modified to
    fit the needs of this project but some apis are still untouched.

    :param html: html markup string.
    :param encoding: optional explicit declaration of encoding type of that web page
    :param element: Used internally: PyQuery object or raw html.
    """

    def __init__(self, html=None, encoding=None, element=None):
        self._lxml = None
        self._pq = None
        self._soup = None
        self._html = html  # represents your raw html
        self._encoding = encoding  # represents your provided encoding
        self.element = element  # internal lxml element
        self._decoded_html = False  # internal switch to tell if html has been decoded
        self.default_encoding = 'iso-8859-1'  # a standard encoding defined by www

    @property
    def raw_html(self):
        """Bytes representation of the HTML content.
        (`learn more <http://www.diveintopython3.net/strings.html>`_).
        """
        if self._html:
            return self._html
        else:
            return tostring(self.element, encoding=self.encoding)

    @raw_html.setter
    def raw_html(self, html):
        """Property setter for raw_html. Type can be bytes."""
        self._html = html

    @property
    def html(self):
        """Unicode representation of the HTML content."""
        if self._html:
            return self.decode()
        else:
            return tostring(self.element, encoding='unicode')

    @html.setter
    def html(self, html):
        """Property setter for self.html"""
        if not isinstance(html, str):
            raise TypeError
        self._html = html
        self.decode()

    def encode(self, encoding=None, errors='xmlcharrefreplace'):
        """Returns the html encoded with specified encoding."""
        return self.html.encode(encoding=encoding, errors=errors)

    def decode(self):
        """Decodes the html set to this object and returns used encoding and decoded html."""
        self._encoding, html = self.decode_html(self._html, self._encoding, self.default_encoding)
        return html

    @staticmethod
    def decode_html(html_string, encoding=None, default_encoding='iso-8859-1'):
        """Decodes a html string into a unicode string.
        If explicit encoding is defined then
        it would use it otherwise it will decoding it using
        beautiful soups UnicodeDammit feature,
        otherwise it will use w3lib to decode the html.

        Returns a two tuple with (<encoding>, <decoded unicode string>)

        :rtype: (str, str)
        :returns: (used-encoding, unicode-markup)
        """

        tried = [encoding, default_encoding]

        try:
            LOGGER.info("Using default Codec on raw_html!")

            converted = UnicodeDammit(html_string, [encoding], is_html=True)

            if not converted.unicode_markup:
                tried += converted.tried_encodings
                raise UnicodeDecodeError

            return converted.original_encoding, converted.unicode_markup

        except UnicodeDecodeError:
            try:
                # This method will definitely decode the html though
                # the result could be corrupt. But if you getting a
                # corrupt html output then you definitely have to
                # manually provide the encoding.
                enc, unc = html_to_unicode(None, html_body_str=html_string,
                                           default_encoding=default_encoding)

                return enc, unc

            except UnicodeDecodeError:
                LOGGER.exception(
                    "Unicode decoder failed to decode html!"
                    "Encoding tried by default enc: [%s]"
                    "Trying fallback..." % ','.join(tried)
                )
                raise

    @property
    def encoding(self):
        """The encoding string to be used, extracted from the HTML and
        :class:`HTMLResponse <HTMLResponse>` headers.
        """
        if self._encoding is None:
            self.decode()
        return self._encoding

    @encoding.setter
    def encoding(self, enc):
        """Property setter for self.encoding."""
        self._encoding = enc

    @property
    def lxml(self):
        """Parses the decoded self.html contents after decoding it by itself
        decoding detector (default) or decoding it using provided self.default_encoding.
        """
        if self._lxml is None:
            self._lxml = fromstring(self.html)
        return self._lxml

    def _write_mark(self, text):
        """Writes a watermark comment in the parsed html."""
        if self.lxml is not None:
            self.lxml.insert(0, Comment(text))

    @property
    def bs4(self):
        """BeautifulSoup object under the hood.
        Read more about beautiful_soup at https://www.crummy.com/software/BeautifulSoup/doc
        """
        if self._soup is None:
            self._soup = BeautifulSoup(self.raw_html, 'lxml')
        return self._soup

    @property
    def pq(self):
        """`PyQuery <https://pythonhosted.org/pyquery/>`_ representation
        of the :class:`Element <Element>` or :class:`HTML <HTML>`.
        """
        if self._pq is None:
            self._pq = PyQuery(self.lxml)

        return self._pq

    @property
    def text(self):
        """The text content of the
        :class:`Element <Element>` or :class:`HTML <HTML>`.
        """
        return self.pq.text()

    @property
    def full_text(self):
        """The full text content (including links) of the
        :class:`Element <Element>` or :class:`HTML <HTML>`.
        """
        return self.lxml.text_content()

    def find(self, selector="*", containing=None, clean=False, first=False,
             _encoding=None):
        """Given a CSS Selector, returns a list of
        :class:`Element <Element>` objects or a single one.

        :param selector: CSS Selector to use.
        :param clean: Whether or not to sanitize the found HTML of ``<script>`` and ``<style>`` tags.
        :param containing: If specified, only return elements that contain the provided text.
        :param first: Whether or not to return just the first result.
        :param _encoding: The encoding format.

        Example CSS Selectors:

        - ``a``
        - ``a.someClass``
        - ``a#someID``
        - ``a[target=_blank]``

        See W3School's `CSS Selectors Reference
        <https://www.w3schools.com/cssref/css_selectors.asp>`_
        for more details.

        If ``first`` is ``True``, only returns the first
        :class:`Element <Element>` found.
        """

        # Convert a single containing into a list.
        if isinstance(containing, str):
            containing = [containing]
        if not isinstance(selector, str):
            raise TypeError("Expected string, got %r" % type(selector))

        encoding = _encoding or self.encoding
        elements = [
            Element(element=found, default_encoding=encoding)
            for found in self.pq(selector)
        ]

        if containing:
            elements_copy = list(elements)
            elements = []

            for element in elements_copy:
                if any([c.lower() in element.full_text.lower() for c in containing]):
                    elements.append(element)

            elements.reverse()

        # Sanitize the found HTML.
        if clean:
            elements_copy = list(elements)
            elements = []

            for element in elements_copy:
                element.raw_html = tostring(cleaner.clean_html(element.lxml))
                elements.append(element)

        if first and len(elements) > 0:
            return elements[0]
        else:
            return elements

    def xpath(self, selector, clean=False, first=False, _encoding=None):
        """Given an XPath selector, returns a list of
        :class:`Element <Element>` objects or a single one.

        :param selector: XPath Selector to use.
        :param clean: Whether or not to sanitize the found HTML of ``<script>`` and ``<style>`` tags.
        :param first: Whether or not to return just the first result.
        :param _encoding: The encoding format.

        If a sub-selector is specified (e.g. ``//a/@href``), a simple
        list of results is returned.

        See W3School's `XPath Examples
        <https://www.w3schools.com/xml/xpath_examples.asp>`_
        for more details.

        If ``first`` is ``True``, only returns the first
        :class:`Element <Element>` found.
        """
        if not isinstance(selector, str):
            raise TypeError("Expected string, got %r" % type(selector))

        selected = self.lxml.xpath(selector)

        elements = [
            Element(element=selection, default_encoding=_encoding or self.encoding)
            if not issubclass(selection, str) else str(selection)
            for selection in selected
        ]

        # Sanitize the found HTML.
        if clean:
            elements_copy = list(elements)
            elements = []

            for element in elements_copy:
                element.raw_html = tostring(cleaner.clean_html(element.lxml))
                elements.append(element)

        if first and len(elements) > 0:
            return elements[0]
        else:
            return elements

    def search(self, template):
        """Search the :class:`Element <Element>` for the given Parse template.

        :param template: The Parse template to use.
        """
        if not isinstance(template, str):
            raise TypeError("Expected string, got %r" % type(template))

        return parse_search(template, self.html)

    def search_all(self, template):
        """Search the :class:`Element <Element>` (multiple times) for the given parse
        template.

        :param template: The Parse template to use.
        """
        if not isinstance(template, str):
            raise TypeError("Expected string, got %r" % type(template))

        return [r for r in findall(template, self.html)]


class Element(MultiParser):  # pragma: no cover
    """An element of HTML.

    :param element: The element from which to base the parsing upon.
    :param default_encoding: Which encoding to default to.
    """

    def __init__(self, element, default_encoding=None):
        super(Element, self).__init__(element=element, encoding=default_encoding)
        self.element = element
        self.tag = element.tag
        self.lineno = element.sourceline
        self._attrs = None

    def __repr__(self):
        attrs = ['{}={}'.format(attr, repr(self.attrs[attr])) for attr in self.attrs]
        return "<Element {} {}>".format(repr(self.element.tag), ' '.join(attrs))

    @property
    def attrs(self):
        """Returns a dictionary of the attributes of the :class:`Element <Element>`
        (`learn more <https://www.w3schools.com/tags/ref_attributes.asp>`_).
        """
        if self._attrs is None:
            d = {}
            for k, v in self.element.items():
                d[k] = v
            self._attrs = d

            # Split class and rel up, as there are usually many of them:
            for attr in ['class', 'rel']:
                if attr in self._attrs:
                    self._attrs[attr] = tuple(self._attrs[attr].split())

        return self._attrs
