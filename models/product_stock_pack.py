from odoo import api, fields, models, _ 
from odoo.addons import decimal_precision as dp
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_round, float_compare


class PackOperation(models.Model):
    _inherit = "stock.pack.operation"
   
    location_id = fields.Many2one('stock.location', 'Source Location', related='product_id.location_id', required=False)
    location_dest_id = fields.Many2one('stock.location', 'Destination Location', required=True, related='product_id.picking_destination_location_id')
    picking_source_location_id = fields.Many2one('stock.location', string='Picking Source Location', related='product_id.picking_source_location_id')
    picking_destination_location_id = fields.Many2one('stock.location', string="Picking Destination", related='product_id.picking_destination_location_id')


class ProductProduct(models.Model):
    _inherit = "product.template"
    
    picking_source_location_id = fields.Many2one('stock.location',  string='Picking Source Location', required=True)#, related='picking_id.location_id')
    picking_destination_location_id = fields.Many2one('stock.location',  string='Picking Destination', required=True)#, related='picking_id.location_dest_id')
    

class ReturnPicking(models.TransientModel):
    _inherit = 'stock.return.picking'
    stock_id = fields.Many2one('stock.picking', compute="get_move_picking_id")
    
    @api.depends('product_return_moves')
    def get_move_picking_id(self):
        for rec in self.product_return_moves:
            self.stock_id = rec[0].move_id.picking_id.id
    
    @api.multi
    def create_returns(self):
        res = super(ReturnPicking, self).create_returns()
        goods_return = self.env['ikoyi.goods_return'].search([('stock_id','=', self.stock_id.id)])
        for tec in goods_return:
            if tec.state == 'update':
                tec.storer_send_vendor()
        return res
 
    # @api.multi
    # def _create_returns(self):
    #     res = super(ReturnPicking, self)._create_returns()
        
    @api.multi
    def _create_returns(self):
        # TDE FIXME: store it in the wizard, stupid
        picking = self.env['stock.picking'].browse(self.env.context['active_id'])

        return_moves = self.product_return_moves.mapped('move_id')
        unreserve_moves = self.env['stock.move']
        for move in return_moves:
            to_check_moves = self.env['stock.move'] | move.move_dest_id
            while to_check_moves:
                current_move = to_check_moves[-1]
                to_check_moves = to_check_moves[:-1]
                if current_move.state not in ('done', 'cancel') and current_move.reserved_quant_ids:
                    unreserve_moves |= current_move
                split_move_ids = self.env['stock.move'].search([('split_from', '=', current_move.id)])
                to_check_moves |= split_move_ids

        if unreserve_moves:
            unreserve_moves.do_unreserve()
            # break the link between moves in order to be able to fix them later if needed
            unreserve_moves.write({'move_orig_ids': False})

        # create new picking for returned products
        picking_type_id = picking.picking_type_id.return_picking_type_id.id or picking.picking_type_id.id
        new_picking = picking.copy({
            'move_lines': [],
            'picking_type_id': picking_type_id,
            'state': 'draft',
            'origin': picking.name,
            'location_id': picking.location_dest_id.id,
            'location_dest_id': self.location_id.id})
        new_picking.message_post_with_view('mail.message_origin_link',
            values={'self': new_picking, 'origin': picking},
            subtype_id=self.env.ref('mail.mt_note').id)

        returned_lines = 0
        for return_line in self.product_return_moves:
            if not return_line.move_id:
                raise UserError(_("You have manually created product lines, please delete them to proceed"))
            new_qty = return_line.quantity
            if new_qty:
                # The return of a return should be linked with the original's destination move if it was not cancelled
                if return_line.move_id.origin_returned_move_id.move_dest_id.id and return_line.move_id.origin_returned_move_id.move_dest_id.state != 'cancel':
                    move_dest_id = return_line.move_id.origin_returned_move_id.move_dest_id.id
                else:
                    move_dest_id = False

                returned_lines += 1
                return_line.move_id.copy({
                    'product_id': return_line.product_id.id,
                    'product_uom_qty': new_qty,
                    'picking_id': new_picking.id,
                    'state': 'draft',
                    'location_id': return_line.move_id.location_dest_id.id,
                    'location_dest_id': self.location_id.id or return_line.move_id.location_id.id,
                    'picking_type_id': picking_type_id,
                    'warehouse_id': picking.picking_type_id.warehouse_id.id,
                    'origin_returned_move_id': return_line.move_id.id,
                    'procure_method': 'make_to_stock',
                    'move_dest_id': move_dest_id,
                })

        if not returned_lines:
            raise UserError(_("Please specify at least one non-zero quantity."))
        
        goods_in = self.env['ikoyi.goods_return'].search([('stock_id','=', self.stock_id.id)])
        goods_in.write({'stock_in_id': new_picking.id})
        new_picking.action_confirm()
        new_picking.action_assign()
        
        return new_picking.id, picking_type_id
      

    #  <button name="%(stock.act_stock_return_picking)d" states="update" string="Return"
    #                 type="action" groups="base.group_user"/>
                    