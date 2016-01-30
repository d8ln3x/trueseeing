# Vulnerabilities:
# * Security: Cross-site scripting
# * Security: Escaratable cross-site scripting (API < 17)
# * Security: Cross-site Request Forgery
# * Security: SQL injection
# * Security: Server-side JavaScript injection
# * Security: TLS interception
# * Security: Arbitrary Large-area WebView Overwrite
# * Security: Insecure permissions
# * Security: Insecure libraries
# * Security: Improper annotations
# * Security: Root introspection
# * Security: Low reverse-enginnering resistance (dex2jar+jad, androguard)

import binascii
import functools
import itertools
import lxml.etree as ET
import shutil
import re
import math
import base64
import os
import logging

from trueseeing.flow.code import OpMatcher, InvocationPattern
from trueseeing.flow.data import DataFlows
from trueseeing.signature.base import Detector

log = logging.getLogger(__name__)

class SecurityFilePermissionDetector(Detector):
  option = 'security-file-permission'
  
  def do_detect(self):
    for cl in self.context.analyzed_classes():
      for k in OpMatcher(cl.ops, InvocationPattern('invoke-virtual', 'Landroid/content/Context;->openFileOutput\(Ljava/lang/String;I\)')).matching():
        try:
          target_val = int(DataFlows.solved_constant_data_in_invocation(k, 1), 16)
          if target_val & 3:
            yield self.warning_on(name='%(name)s#%(method)s' % dict(name=self.context.class_name_of_dalvik_class_type(cl.qualified_name()), method=k.method_.v.v), row=0, col=0, desc='insecure file permission: %s' % {1:'MODE_WORLD_READABLE', 2:'MODE_WORLD_WRITABLE'}[target_val], opt='-Wsecurity-file-permission')
        except (DataFlows.NoSuchValueError):
          pass

class SecurityTlsInterceptionDetector(Detector):
  option = 'security-tls-interception'
  
  def do_detect(self):
    marks = []

    pins = set()
    for cl in self.context.analyzed_classes():
      # XXX crude detection
      for m in (m for m in cl.methods if re.match('checkServerTrusted', m.qualified_name())):
        for k in OpMatcher(m.ops, InvocationPattern('invoke-virtual', 'Ljava/security/MessageDigest->digest')).matching():
          pins.add(cl)

    if not pins:
      yield self.warning_on(name='(global)', row=0, col=0, desc='insecure TLS connection', opt='-Wsecurity-tls-interception')
    else:
      for cl in self.context.analyzed_classes():
        # XXX crude detection
        for k in OpMatcher(cl.ops, InvocationPattern('invoke-virtual', 'Ljavax/net/ssl/SSLContext->init')).matching():
          if not DataFlows.solved_typeset_in_invocation(k, 2) & pins:
            yield self.warning_on(name='%s#%s' % (self.context.class_name_of_dalvik_class_type(cl.qualified_name()), k.method_.v.v), row=0, col=0, desc='insecure TLS connection', opt='-Wsecurity-tls-interception')
        else:
          yield self.warning_on(name='(global)', row=0, col=0, desc='insecure TLS connection', opt='-Wsecurity-tls-interception')


class LayoutSizeGuesser:
  xmlns_android = '{http://schemas.android.com/apk/res/android}'
  table = {'small':(320.0, 426.0), 'normal':(320.0, 470.0), 'large':(480.0, 640.0), 'xlarge':(720.0, 960.0)}
  
  def guessed_size(self, t, path):
    def dps_from_modifiers(mods):
      try:
        x, y = self.table[list(mods & self.table.keys())[0]]
      except (IndexError, KeyError):
        x, y = self.table['large']
      if 'land' in mods:
        return (y, x)
      else:
        return (x, y)

    def width_of(e):
      return e.attrib['{0}layout_width'.format(self.xmlns_android)]

    def height_of(e):
      return e.attrib['{0}layout_height'.format(self.xmlns_android)]
    
    def is_bound(x):
      return x not in ('fill_parent', 'match_parent', 'wrap_content')

    def guessed_dp(x, dp):
      if is_bound(x):
        try:
          return int(re.sub(r'di?p$', '', x)) / float(dp)
        except ValueError:
          print("check_security_arbitrary_webview_overwrite: guessed_size: guessed_dp: warning: ignoring non-dp suffix ({!s})".format(x))
          return int(re.sub(r'[^0-9-]', '', x)) / float(dp)
      else:
        return dp

    def self_and_containers_of(e):
      yield e
      e = e.getparent()
      if e is not None:
        self_and_containers_of(e)

    def modifiers_in(path):
      return [set(c.split('-')) for c in path.split(os.sep) if 'layout' in c][0]
      
    dps = dps_from_modifiers(modifiers_in(path))
    for e in self_and_containers_of(t):
      if any(is_bound(x) for x in (width_of(e), height_of(e))):
        return guessed_dp(width_of(e), dps[0]) * guessed_dp(height_of(e), dps[1])
    else:
      return 1.0

class SecurityArbitraryWebViewOverwriteDetector(Detector):
  option = 'security-arbitrary-webview-overwrite'
  
  xmlns_android = '{http://schemas.android.com/apk/res/android}'
  
  def do_detect(self):
    targets = {'WebView','XWalkView','GeckoView'}
    seed = '|'.join(targets)

    more = True
    while more:
      more = False
      for cl in (c for c in self.context.analyzed_classes() if (c.super_.v in targets) or (re.search(seed, c.super_.v))):
        name = self.context.class_name_of_dalvik_class_type(cl.qualified_name())
        if name not in targets:
          targets.add(self.context.class_name_of_dalvik_class_type(cl.qualified_name()))
          more = True

    for fn in (n for n in self.context.disassembled_resources() if 'layout' in n):
      with open(fn, 'r') as f:
        r = ET.parse(f).getroot()
        for t in functools.reduce(lambda x,y: x+y, (r.xpath('//%s' % c.replace('$', '_')) for c in targets)):
          size = LayoutSizeGuesser().guessed_size(t, fn)
          if size > 0.5:
            yield self.warning_on(name=self.context.source_name_of_disassembled_resource(fn), row=0, col=0, desc='arbitrary WebView content overwrite: {0} (score: {1:.02f})'.format(t.attrib['{0}id'.format(self.xmlns_android)], size), opt='-Wsecurity-arbitrary-webview-overwrite')