from datetime import datetime, timedelta
import time
from dateutil.relativedelta import relativedelta
import base64
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.tools.float_utils import float_is_zero, float_compare
from odoo.tools.misc import formatLang
from odoo.addons.base.res.res_partner import WARNING_MESSAGE, WARNING_HELP
import odoo.addons.decimal_precision as dp
from odoo import models, fields, api, _, SUPERUSER_ID
from odoo.exceptions import ValidationError
from odoo import http

class EmptyBottle(models.Model):
    _name = "costing.empty.bottle"

    def get_url(self, id, name):
        base_url = http.request.env['ir.config_parameter'].sudo().get_param('web.base.url')
        base_url += '/web#id=%d&view_type=form&model=%s' % (id, name)
        return "<a href={}> </b>Click<a/>. ".format(base_url)
        
    @api.model
    def create(self, vals):
		vals['name'] = self.env['ir.sequence'].next_by_code('costing.empty.bottle') 
		return super(EmptyBottle, self).create(vals)
    name = fields.Char(
        'Reference')
    employee_id = fields.Many2one('hr.employee',string='employee', 
    default=lambda self: self.env['hr.employee'].search([('user_id', '=', self.env.uid)], limit=1), required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('manager', 'Manager'),
        ('food_manager', 'Food_Manager'),
        ('approve', 'Approve'),
        ('done', 'Done'),
        ('refuse', 'Refuse'),
        ('cancel', 'Cancelled'),
    ], string='Status', readonly=True, index=True, copy=False, default='draft', track_visibility='onchange')
    date = fields.Datetime(
        'Date',
        readonly=True,
        default=fields.Datetime.now)
    direct_to =  fields.Many2one('hr.employee',string='Direct To',required=True, states={'draft': [('readonly', False)]})
    order_line = fields.One2many('empty.bottle.line','empty_id', string="order line")
    notes = fields.Text('Note')

    @api.multi
    def button_self_manager(self): # draft
        self.write({'state':'manager'})
        self.send_mail('Sent')
    
    def check_validator(self):
        if self.direct_to.user_id.id != self.env.uid:
            raise ValidationError('You are not allowed to Approved this Record.\n Ensure this record is directed to you.')
    
    def store_user_validator(self):
        store_officer = 'ikoyi_module.store_keeper_ikoyi'
        store_manager = 'ikoyi_module.inventory_manager_ikoyi'
        user = self.env['res.users'].search([('id', '=', self.env.uid)])
        if not (user.has_group(store_officer) or user.has_group(store_manager)):
            raise ValidationError('You are not allowed to Complete this Record.\n You need To be given Right as Store office or Manager.')
    
    @api.multi
    def button_approve(self): # managr
        self.check_validator()
        self.write({'state':'approve'})
        self.send_mail('Approved')

    @api.multi
    def complete_button(self): #approve
        self.check_recieved_line()
        self.store_user_validator()
        self.send_mail_all()
        return self.update_stock()
         


    @api.multi
    def button_refuse(self):
        self.state = ({'state':'refuse'})

    def check_recieved_line(self):
        if self.mapped('order_line').filtered(lambda a: a.recieve_qty <= 0):
            raise ValidationError('Please ensure all received qty is greater than one')
    
    def get_internal_picking_type(self):
        pick_type = self.env['stock.picking.type'].search(['|', ('code', '=', 'internal'), ('name', '=', 'Internal Transfers')], limit=1)
        return pick_type

    def created_moves(self):
        lines = {}
        for line in self.order_line:
            lines = {'name':"Bottles Request for %s" %line.product_id.name,
                    'product_id':line.product_id.id,
                     'product_uom_qty': line.recieve_qty, # if self.state == "approve" else line.recieve_qty,
                     'product_uom': line.label.id,
                     'location_id': self.get_internal_picking_type().default_location_src_id.id, #locate_out[0].id,
                     'location_dest_id': self.get_internal_picking_type().default_location_src_id.id,
                     'scrapped': self.get_internal_picking_type().default_location_src_id.scrap_location, 
                     'state': 'draft',
                     'date_expected': fields.Datetime.now(),
                     'date': fields.Datetime.now(),

                     }

            return [(0, 0, lines)]


    @api.multi
    def update_stock(self):
        self.write({'state':'done'})
        resp = {
            'type': 'ir.actions.act_window',
            'name': _('Request Reference'),
            'res_model': 'stock.picking',
            'view_type': 'form',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_picking_type_id': self.get_internal_picking_type().id,
                        # 'default_min_date': datetime.now(),
                        'default_branch': self.env.user.branch_id.id,
                        'default_picking_type_code': self.get_internal_picking_type().code,
                        'default_move_lines': self.created_moves()
                        }
        }
        return resp

    def send_mail_all(self):
        email_from = self.env.user.email
        group_user_id2 = self.env.ref('ikoyi_module.costing_officer_ikoyi').id
        group_user_id = self.env.ref('ikoyi_module.costing_manager_ikoyi').id
        group_user_id3 = self.env.ref('stock_account.group_inventory_valuation').id
        bodyx = "Dear Sir, <br/>A request with reference Name: {} have been completed by {}. Please kindly {} Review. <br/> Regards".format(self.name, self.employee_id.name, self.get_url(self.id, self._name))
    
        self.mail_sending_for_three(
            email_from,
            group_user_id,
            group_user_id2,
            group_user_id3,
            bodyx)
         
    def send_mail(self, types):
        email_from = self.env.user.email
        mail_to=self.direct_to.work_email
        subject = "Ikoyi Club Empty Bottle Return Notification"
        bodyx = "This is to notify you that a request to return an empty bottle\
             with reference {} have been {} on the date \
        {}. Kindly {} to view<br/> Thanks".format(self.name, types, fields.Date.today(), self.get_url(self.id, self._name))
        self.mail_sending_one(email_from, mail_to, bodyx, subject)


    def mail_sending_one(self, email_from, mail_to, bodyx, subject):
		for order in self:
			mail_tos = str(mail_to)
			email_froms = email_from # "Ikoyi Club: " + " <" + str(email_from) + ">"
			subject = subject
			mail_data = {
                'email_from': email_froms,
                'subject': subject,
                'email_to': mail_tos,
                'reply_to': email_from,
                'body_html': bodyx,
                        }
			mail_id = order.env['mail.mail'].create(mail_data)
			order.env['mail.mail'].send(mail_id)
 
    def mail_sending_for_three(
            self,
            email_from,
            group_user_id,
            group_user_id2,
            group_user_id3,
            bodyx):
        from_browse = self.env.user.name
        groups = self.env['res.groups']
        for order in self:
            group_users = groups.search([('id', '=', group_user_id)])
            group_users2 = groups.search([('id', '=', group_user_id2)])
            group_users3 = groups.search([('id', '=', group_user_id3)])
            group_emails = group_users.users
            group_emails2 = group_users2.users
            group_emails3 = group_users3.users

            append_mails = []
            append_mails_to = []
            append_mails_to3 = []
            for group_mail in group_emails:
                append_mails.append(group_mail.login)

            for group_mail2 in group_emails2:
                append_mails_to.append(group_mail2.login)

            for group_mail3 in group_emails3:
                append_mails_to3.append(group_mail3.login)

            all_mails = append_mails + append_mails_to + append_mails_to3
            print(all_mails)
            email_froms = str(from_browse) + " <" + str(email_from) + ">"
            mail_sender = (', '.join(str(item)for item in all_mails))
 
            subject = "Bottle Return Notification"

            mail_data = {
                'email_from': email_froms,
                'subject': subject,
                'email_to': mail_sender,
                'email_cc': mail_sender,  # + (','.join(str(extra)),
                'reply_to': email_from,
                'body_html': bodyx
            }
            mail_id = order.env['mail.mail'].create(mail_data)
            order.env['mail.mail'].send(mail_id)

    

class EmptyBottleLine(models.Model):
    _name = "empty.bottle.line"

    def change_uom(self):
        uom = self.env['product.uom'].search([('name', '=', 'Unit(s)')], limit=1)
        return uom.id
    empty_id = fields.Many2one('costing.empty.bottle',string='Empty bottle ID')
    product_id = fields.Many2one('product.product',)  
    recieve_qty =fields.Integer(string='Return Qty')
    label = fields.Many2one(
        'product.uom',
        string='UOM',
        default=change_uom,
        required=True) 


    
# class Cost_Management(models.Model):
#     _name = "costing.management"
#     _inherit = ['mail.thread', 'ir.needaction_mixin']
#     _description = "Cost Management"
#     _order = "id desc"

#     def get_url(self, id, name):
#         base_url = http.request.env['ir.config_parameter'].sudo().get_param('web.base.url')
#         base_url += '/web#id=%d&view_type=form&model=%s' % (id, name)
#         return "<a href={}> </b>Click<a/>. ".format(base_url)

#     def _get_all_stocks(self):
#         lists = []
#         stock_obj = self.env["stock.quant"]
#         search_stock_quant = stock_obj.search([('qty','>',0)])

#         for rec in search_stock_quant:
#             lists.append (rec.id)
#         return lists    
        
#     stock_qty = fields.Many2many(
#         'stock.quant', string="Stocks (GRN) ID", 
#         default=lambda self : self._get_all_stocks(),
#         )


 
    










