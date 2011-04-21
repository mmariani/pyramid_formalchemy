# -*- coding: utf-8 -*-
from webhelpers.paginate import Page
from sqlalchemy.orm import class_mapper
from formalchemy.fields import _pk
from formalchemy.fields import _stringify
from formalchemy import Grid, FieldSet
from formalchemy.i18n import get_translator
from formalchemy.fields import Field
from formalchemy import fatypes
from pyramid.renderers import get_renderer
from pyramid.response import Response
from pyramid import httpexceptions as exc
from pyramid.exceptions import NotFound
from pyramid_formalchemy.utils import TemplateEngine
import logging

try:
    from formalchemy.ext.couchdb import Document
except ImportError:
    Document = None

try:
    import simplejson as json
except ImportError:
    import json

class Session(object):
    """A abstract class to implement other backend than SA"""
    def add(self, record):
        """add a record"""
    def update(self, record):
        """update a record"""
    def delete(self, record):
        """delete a record"""
    def commit(self):
        """commit transaction"""

class ModelView(object):
    """A RESTful view bound to a model"""

    engine = TemplateEngine()
    pager_args = dict(link_attr={'class': 'ui-pager-link ui-state-default ui-corner-all'},
                      curpage_attr={'class': 'ui-pager-curpage ui-state-highlight ui-corner-all'})

    def __init__(self, context, request):
        self.context = context
        self.request = request

        self.FieldSet = FieldSet
        self.Grid = Grid

    @property
    def model_name(self):
        """return ``model_name`` from ``pylons.routes_dict``"""
        try:
            return self.request.model_name
        except AttributeError:
            return None

    def Session(self):
        """return a Session object. You **must** override this."""
        return self.request.session_factory()

    def models(self, **kwargs):
        """Models index page"""
        request = self.request
        models = {}
        if isinstance(request.model, list):
            for model in request.model:
                key = model.__name__
                models[key] = request.fa_url(key, request.format)
        else:
            for key, obj in request.model.__dict__.iteritems():
                if not key.startswith('_'):
                    if Document is not None:
                        try:
                            if issubclass(obj, Document):
                                models[key] = request.fa_url(key, request.format)
                                continue
                        except:
                            pass
                    try:
                        class_mapper(obj)
                    except:
                        continue
                    if not isinstance(obj, type):
                        continue
                    models[key] = request.fa_url(key, request.format)
        return self.render(models=models)

    def sync(self, fs, id=None):
        """sync a record. If ``id`` is None add a new record else save current one.

        Default is::

            S = self.Session()
            if id:
                S.merge(fs.model)
            else:
                S.add(fs.model)
            S.commit()
        """
        S = self.Session()
        if id:
            S.merge(fs.model)
        else:
            S.add(fs.model)

    def breadcrumb(self, fs=None, **kwargs):
        """return items to build the breadcrumb"""
        items = []
        request = self.request
        model_name = request.model_name
        id = request.model_id
        items.append((request.fa_url(), 'root', 'root_url'))
        if self.model_name:
            items.append((request.fa_url(model_name), model_name, 'model_url'))
        if id and hasattr(fs.model, '__unicode__'):
            items.append((request.fa_url(model_name, id), u'%s' % self.context.get_instance(), 'instance_url'))
        elif id:
            items.append((request.fa_url(model_name, id), id, 'instance_url'))
        return items

    def render(self, **kwargs):
        """render the form as html or json"""
        request = self.request
        if request.format != 'html':
            meth = getattr(self, 'render_%s_format' % request.format, None)
            if meth is not None:
                return meth(**kwargs)
            else:
                raise NotFound()
        kwargs.update(
                      main = get_renderer('pyramid_formalchemy:templates/admin/master.pt').implementation(),
                      model_name=self.model_name,
                      breadcrumb=self.breadcrumb(**kwargs),
                      F_=get_translator().gettext)
        return kwargs

    def render_grid(self, **kwargs):
        """render the grid as html or json"""
        return self.render(is_grid=True, **kwargs)

    def render_json_format(self, fs=None, **kwargs):
        request = self.request
        request.override_renderer = 'json'
        if fs:
            try:
                fields = fs.jsonify()
            except AttributeError:
                fields = dict([(field.renderer.name, field.model_value) for field in fs.render_fields.values()])
            data = dict(fields=fields)
            pk = _pk(fs.model)
            if pk:
                data['item_url'] = request.fa_url(self.model_name, 'json', pk)
        else:
            data = {}
        data.update(kwargs)
        return data

    def render_xhr_format(self, fs=None, **kwargs):
        self.request.response_content_type = 'text/html'
        if fs is not None:
            if 'field' in self.request.GET:
                field_name = self.request.GET.get('field')
                fields = fs.render_fields
                if field_name in fields:
                    field = fields[field_name]
                    return Response(field.render())
                else:
                    raise NotFound()
            return Response(fs.render())
        return Response('')

    def get_page(self, **kwargs):
        """return a ``webhelpers.paginate.Page`` used to display ``Grid``.

        Default is::

            S = self.Session()
            query = S.query(self.context.get_model())
            kwargs = request.environ.get('pylons.routes_dict', {})
            return Page(query, page=int(request.GET.get('page', '1')), **kwargs)
        """
        S = self.Session()
        def get_page_url(page, partial=None):
            url = "%s?page=%s" % (self.request.path, page)
            if partial:
                url += "&partial=1"
            return url
        options = dict(collection=S.query(self.context.get_model()),
                       page=int(self.request.GET.get('page', '1')),
                       url=get_page_url)
        options.update(kwargs)
        collection = options.pop('collection')
        return Page(collection, **options)

    def get(self, id=None):
        """return correct record for ``id`` or a new instance.

        Default is::

            S = self.Session()
            model = self.context.get_model()
            if id:
                model = S.query(model).get(id)
            else:
                model = model()
            raise NotFound()

        """
        S = self.Session()
        model = self.context.get_model()
        if id:
            model = S.query(model).get(id)
        if model:
            return model
        raise NotFound()

    def get_fieldset(self, id=None):
        """return a ``FieldSet`` object bound to the correct record for ``id``.
        """
        request = self.request
        if request.forms and hasattr(request.forms, self.model_name):
            fs = getattr(request.forms, self.model_name)
            fs.engine = fs.engine or self.engine
            return id and fs.bind(self.get(id)) or fs
        fs = self.FieldSet(self.get(id))
        fs.engine = fs.engine or self.engine
        return fs

    def get_add_fieldset(self):
        """return a ``FieldSet`` used for add form.
        """
        fs = self.get_fieldset()
        for field in fs.render_fields.itervalues():
            if field.is_readonly():
                del fs[field.name]
        return fs

    def get_grid(self, model_name=None):
        """return a Grid object

        Default is::

            grid = self.Grid(self.context.get_model())
            grid.engine = self.engine
            self.update_grid(grid)
            return grid
        """
        request = self.request
        model_name = model_name or self.model_name
        if request.forms and hasattr(request.forms, '%sGrid' % model_name):
            g = getattr(request.forms, '%sGrid' % model_name)
            g.engine = g.engine or self.engine
            g.readonly = True
            self.update_grid(g)
            return g
        model = self.context.get_model()
        grid = self.Grid(model)
        grid.engine = self.engine
        self.update_grid(grid)
        return grid


    def update_grid(self, grid):
        """Add edit and delete buttons to ``Grid``"""
        try:
            grid.edit
        except AttributeError:
            def edit_link():
                return lambda item: '''
                <form action="%(url)s" method="GET" class="ui-grid-icon ui-widget-header ui-corner-all">
                <input type="submit" class="ui-grid-icon ui-icon ui-icon-pencil" title="%(label)s" value="%(label)s" />
                </form>
                ''' % dict(url=self.request.fa_url(self.model_name, _pk(item), 'edit'),
                            label=get_translator().gettext('edit'))
            def delete_link():
                return lambda item: '''
                <form action="%(url)s" method="POST" class="ui-grid-icon ui-state-error ui-corner-all">
                <input type="submit" class="ui-icon ui-icon-circle-close" title="%(label)s" value="%(label)s" />
                </form>
                ''' % dict(url=self.request.fa_url(self.model_name, _pk(item), 'delete'),
                           label=get_translator().gettext('delete'))
            grid.append(Field('edit', fatypes.String, edit_link()))
            grid.append(Field('delete', fatypes.String, delete_link()))
            grid.readonly = True

    def listing(self, **kwargs):
        """listing page"""
        page = self.get_page(**kwargs)
        fs = self.get_grid()
        fs = fs.bind(instances=page)
        fs.readonly = True
        if self.request.format == 'json':
            values = []
            request = self.request
            for item in page:
                pk = _pk(item)
                fs._set_active(item)
                value = dict(id=pk,
                             item_url=request.fa_url(request.model_name, pk))
                if 'jqgrid' in request.GET:
                    fields = [_stringify(field.render_readonly()) for field in fs.render_fields.values()]
                    value['cell'] = [pk] + fields
                else:
                    value.update(dict([(field.key, field.model_value) for field in fs.render_fields.values()]))
                values.append(value)
            return self.render_json_format(rows=values,
                                           records=len(values),
                                           total=page.page_count,
                                           page=page.page)
        if 'pager' not in kwargs:
            pager = page.pager(**self.pager_args)
        else:
            pager = kwargs.pop('pager')
        return self.render_grid(fs=fs, id=None, pager=pager)

    def create(self):
        """REST api"""
        request = self.request
        S = self.Session()
        fs = self.get_add_fieldset()

        if request.format == 'json' and request.method == 'PUT':
            data = json.load(request.body_file)
        else:
            data = request.POST

        try:
            fs = fs.bind(data=data, session=S)
        except:
            # non SA forms
            fs = fs.bind(self.context.get_model(), data=data, session=S)
        if fs.validate():
            fs.sync()
            self.sync(fs)
            S.flush()
            if request.format == 'html':
                if request.is_xhr:
                    response.content_type = 'text/plain'
                    return ''
                next = request.POST.get('next') or request.fa_url(request.model_name)
                return exc.HTTPFound(
                    location=next)
            else:
                fs.rebind(fs.model, data=None)
                return self.render(fs=fs)
        return self.render(fs=fs, action='new', id=None)

    def delete(self, **kwargs):
        """REST api"""
        request = self.request
        id = request.model_id
        record = self.get(id)
        if record:
            S = self.Session()
            S.delete(record)
        if request.format == 'html':
            if request.is_xhr:
                response = Response()
                response.content_type = 'text/plain'
                return response
            return exc.HTTPFound(location=request.fa_url(request.model_name))
        return self.render(id=id)

    def show(self):
        """REST api"""
        id = self.request.model_id
        fs = self.get_fieldset(id=id)
        fs.readonly = True
        return self.render(fs=fs, action='show', id=id)

    def new(self, **kwargs):
        """REST api"""
        fs = self.get_add_fieldset()
        fs = fs.bind(session=self.Session())
        return self.render(fs=fs, action='new', id=None)

    def edit(self, id=None, **kwargs):
        """REST api"""
        id = self.request.model_id
        fs = self.get_fieldset(id)
        return self.render(fs=fs, action='edit', id=id)

    def update(self, **kwargs):
        """REST api"""
        request = self.request
        S = self.Session()
        id = request.model_id
        fs = self.get_fieldset(id)
        if not request.POST:
            raise ValueError(request.POST)
        fs = fs.bind(data=request.POST)
        if fs.validate():
            fs.sync()
            self.sync(fs, id)
            S.flush()
            if request.format == 'html':
                if request.is_xhr:
                    response.content_type = 'text/plain'
                    return ''
                return exc.HTTPFound(
                        location=request.fa_url(request.model_name, _pk(fs.model)))
            else:
                return self.render(fs=fs, status=0)
        if request.format == 'html':
            return self.render(fs=fs, action='edit', id=id)
        else:
            return self.render(fs=fs, status=1)

