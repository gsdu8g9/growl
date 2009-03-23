#!/usr/bin/env python
#
# vim:syntax=python:sw=4:ts=4:expandtab

import os
import sys
import re
import shutil
import datetime

import yaml
import jinja2


class AttrDict(dict):

    def __getattr__(self, name):
        return self[name]

    def __setattr__(self, name, value):
        self[name] = value

    def copy(self):
        return AttrDict(super(AttrDict, self).copy())


class Config(object):
    transformers = {}

    def __init__(self, base, deploy):
        self.BASE_DIR = base
        self.DEPLOY_DIR = deploy
        self.LAYOUT_DIR = os.path.join(base, '_layout')
        self.POST_DIR = os.path.join(base, '_posts')
        self.HOOK_DIR = os.path.join(base, '_hooks')
        self.LIB_DIR = os.path.join(base, '_libs')


class Template(Config):

    RE_YAML = re.compile(r'(^---\s*$(?P<yaml>.*?)^---\s*$)?(?P<content>.*)',
                         re.M | re.S)

    def __init__(self, base, deploy, filename, layouts, context):
        super(Template, self).__init__(base, deploy)
        self.filename = filename
        self.layouts = layouts
        self.context = context.copy()
        self.read_yaml()

    def read_yaml(self):
        self.content = file(self.filename, 'r').read()

        mo = self.RE_YAML.match(self.content)
        if mo and mo.groupdict().get('yaml'):
            self.context.update(yaml.load(mo.groupdict().get('yaml')))
            self.content = mo.groupdict().get('content')

    def transform(self):
        ext = os.path.splitext(self.filename)[-1][1:]
        t = self.transformers.get(ext, lambda x: x)
        return t(self.content)

    def render(self):
        ctx = self.context.copy()
        ctx.content = jinja2.Template(self.transform()).render(ctx)
        layout = self.layouts.get(ctx.layout)
        if layout:
            return jinja2.Template(layout.content).render(ctx)
        else:
            return ctx.content

    def layout(self):
        ctx = self.context.copy()
        ctx.content = self.render()
        layout = self.layouts.get(ctx.layout)
        if layout:
            layout = self.layouts.get(layout.layout)

        while layout != None:
            ctx.content = jinja2.Template(layout.content).render(ctx)
            layout = self.layouts.get(layout.layout)

        return ctx.content

    def write(self, path, content):
        fname = os.path.join(self.DEPLOY_DIR, path)
        dirname = os.path.dirname(fname)
        if not os.path.isdir(dirname):
            os.makedirs(dirname)
        f = file(fname, 'w')
        f.write(content)
        f.close()

    def __getattr__(self, name):
        if not name in self.context:
            raise AttributeError("'%s' object has no attribute '%s'" %
                                    (self.__class__.__name__, name))
        return self.context[name]


class Layout(Template):

    def __init__(self, base, deploy, filename, context):
        super(Layout, self).__init__(base, deploy, filename, {}, context)

        base = os.path.basename(filename)
        ext = os.path.splitext(base)
        self.name = ext[0]

        self.content = self.transform()

    @property
    def layout(self):
        return self.context.get('layout')


class Page(Template):

    def write(self):
        path = os.path.abspath(self.filename)[:-1]
        path = path.replace(os.path.abspath(self.BASE_DIR), '', 1)
        path = path.lstrip(os.path.sep)
        return super(Page, self).write(path, self.layout())


class Post(Template):

    def __init__(self, base, deploy, filename, layout, context):
        super(Post, self).__init__(base, deploy, filename, layout, context)

        base = os.path.basename(filename)
        ext = os.path.splitext(base)

        self.year, self.month, self.day, self.slug = ext[0].split('-', 3)

        self.context.date = self.date
        self.context.url = self.url
        self.context.path = self.path

        cats = ','.join((self.context.get('category', ''),
                         self.context.get('categories', '')))
        if 'category' in self.context:
            del self.context['category']
        if 'categories' in self.context:
            del self.context['categories']
        self.context.categories = [c.strip() for c in cats.split(',') if c]

    @property
    def date(self):
        return datetime.datetime(int(self.year),
                                 int(self.month),
                                 int(self.day))

    @property
    def permalink(self):
        return self.context.get('permalink')

    @property
    def url(self):
        return '/'.join((self.path, self.slug))

    @property
    def path(self):
        return os.path.join(self.year, self.month, self.day)

    @property
    def categories(self):
        return self.context.get('categories')

    def write(self):
        return super(Post, self).write(os.path.join(self.path,
                                                    self.slug,
                                                    'index.html'),
                                       self.layout())

    def __cmp__(self, other):
        return cmp(self.date, other.date)


class Site(Config):

    def __init__(self, base, deploy):
        super(Site, self).__init__(base, deploy)

        self.context = AttrDict()
        self.context.site = AttrDict()
        self.context.site.time = datetime.datetime.now()

        if not self.LIB_DIR in sys.path:
            sys.path.append(self.LIB_DIR)

        self.hooks()

    def hooks(self):
        for f in sorted(os.listdir(self.HOOK_DIR)):
            if f.endswith('.py'):
                execfile(os.path.join(self.HOOK_DIR, f))

    def read(self):
        self.read_layouts()
        self.read_posts()
        self.calc_categories()

    def generate(self):
        self.write_posts()
        self.write_site_content()

    def deploy(self):
        pass

    def read_layouts(self):
        self.layouts = [Layout(self.BASE_DIR,
                               self.DEPLOY_DIR,
                               os.path.join(self.LAYOUT_DIR, f),
                               self.context)
                            for f in os.listdir(self.LAYOUT_DIR)
                                if not f.startswith('__')]
        self.layouts = dict((l.name, l) for l in self.layouts)

    def read_posts(self):
        self.posts = [Post(self.BASE_DIR,
                           self.DEPLOY_DIR,
                           os.path.join(self.POST_DIR, f),
                           self.layouts,
                           self.context)
                          for f in os.listdir(self.POST_DIR)
                              if not f.startswith('__')]
        self.context.site.posts = sorted(self.posts)

    def calc_categories(self):
        self.categories = AttrDict()
        for post in self.posts:
            for cat in post.categories:
                self.categories.setdefault(cat, []).append(post)
        self.context.site.categories = self.categories

    def write_posts(self):
        for p in self.posts:
            p.write()

    def write_site_content(self):
        for root, dirs, files in os.walk(self.BASE_DIR):
            base = root.replace(self.BASE_DIR, '')

            for d in dirs:
                nd = os.path.join(self.DEPLOY_DIR, base, d)
                if not d.startswith('_') and not os.path.isdir(nd):
                    os.makedirs(nd)
            dirs[:] = [d for d in dirs if not d.startswith('_')]

            for f in files:
                if f.endswith('_'):
                    Page(self.BASE_DIR,
                         self.DEPLOY_DIR,
                         os.path.join(root, f),
                         self.layouts,
                         self.context).write()
                else:
                    path = os.path.abspath(root)
                    path = path.replace(os.path.abspath(self.BASE_DIR), '', 1)
                    path = path.lstrip(os.path.sep)
                    path = os.path.join(self.DEPLOY_DIR, path)
                    if not os.path.isdir(path):
                        os.makedirs(path)
                    shutil.copy(os.path.join(root, f), os.path.join(path, f))


def templateFilter(func):
    jinja2.Template('').environment.filters[func.__name__] = func


if __name__ == '__main__':
    if len(sys.argv) == 1:
        print 'syntax: %s <from> [to]' % sys.argv[0]
        sys.exit(1)
    if len(sys.argv) >= 2:
        base = sys.argv[1]
    if len(sys.argv) >= 3:
        deploy = sys.argv[2]
    else:
        deploy = os.path.join(base, '_deploy')

    site = Site(base, deploy)

    site.read()
    site.generate()
    site.deploy()
