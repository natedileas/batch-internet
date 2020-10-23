import logging

import yaml 
import pywebcopy
from pywebcopy.crawler import Crawler
from pywebcopy.webpage import WebPage


def save_webpage(url, project_folder, html=None, project_name=None, 
	encoding=None, reset_config=False, popup=False, **kwargs):
    """
    Easiest way to save any single webpage with images, css and js.
    usage::
        >>> from pywebcopy import save_webpage
        >>> url = 'http://some-url.com/some-page.html'
        >>> download_folder = '/home/users/me/downloads/pages/'
        >>> project_name = 'some-recognisable-name'
        >>> kwargs = {'bypass_robots':True}
        >>> save_webpage(url, download_folder, project_name, **kwargs)
    :param url: url of the web page to work with
    :type url: str
    :param project_folder: folder in which the files will be downloaded
    :type project_folder: str
    :param project_name: name of the project to distinguish it
    :type project_name: str | None
    :param html: file like object or html if available
    :type html: str
    :param encoding: explicit encoding declaration for decoding html
    :type encoding: str
    :param reset_config: whether to reset the config after saving the web page; could be useful if
    you are saving different web pages which are located on different servers.
    :type reset_config: bool
    :param popup: whether or not to open a new tab after saving the webpage.
    :type popup: bool
    """

    #: Set up the global configuration
    pywebcopy.configs.config.setup_config(url, project_folder, project_name, **kwargs)

    #: Create a object of web page
    wp = WebPage()
    wp.url = pywebcopy.configs.config['project_url']
    wp.path = pywebcopy.configs.config['project_folder']

    logging.debug(pywebcopy.configs.config)

    # wp.register_tag_handler('default', None)  # doesn
    wp.deregister_tag_handler('default')

    #: Remove the extra files downloading if requested
    if not pywebcopy.configs.config.get('load_css'):
        wp.deregister_tag_handler('link')
        wp.deregister_tag_handler('style')
    if not pywebcopy.configs.config.get('load_javascript'):
        wp.deregister_tag_handler('script')
    if not pywebcopy.configs.config.get('load_images'):
        wp.deregister_tag_handler('img')

    if html:
        #: only set url in manual mode because its internally
        #: set in the get() method
        wp.set_source(html, encoding)

    else:
        # print("Fetching page")
        wp.get(wp.url)
        # print("Page fetched")

    # If encoding is specified then change it otherwise a default encoding is
    # always internally set by the get() method
    if encoding:
        wp.encoding = encoding

    # TODO remove javasript / ads
    # wp.bs4.find_all()

    # Instruct it to save the complete page
    wp.save_complete()

    # Everything is done! Now archive the files and delete the folder afterwards.
    # if pywebcopy.configs.config['zip_project_folder']:
    #     zip_project(pywebcopy.configs.config['join_timeout'])
    
    return wp


if __name__ == '__main__':
	import sys
	
	# read list of urls / other config
	config = yaml.safe_load(open(sys.argv[1]))
	folder = config['folder']
	pywebcopy_config = config['pywebcopy_config']

	# for each url:
	for site in config['urls']:
		# grab it and save to disk. (pywebcopy?)
		wp = save_webpage(url=site['url'], project_name=site['name'], project_folder=folder, **pywebcopy_config)

		# check disk usage: if above quota, stop or remove old stuff
		path = wp.file_path

	# generate offline reading index.
	