# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

SII_REGIONAL_OFFICES = [
    ("ur_Alt", "Alto Hospicio"),
    ("ur_Anc", "Ancud"),
    ("ur_Ang", "Angol"),
    ("ur_Ant", "Antofagasta"),
    ("ur_Ari", "Arica y Parinacota"),
    ("ur_Ays", "Aysén"),
    ("ur_Buin", "Buin"),
    ("ur_Cal", "Calama"),
    ("ur_Cas", "Castro"),
    ("ur_Cau", "Cauquenes"),
    ("ur_Cha", "Chaitén"),
    ("ur_Chn", "Chañaral"),
    ("ur_ChC", "Chile Chico"),
    ("ur_Chi", "Chillán"),
    ("ur_Coc", "Cochrane"),
    ("ur_Cop", "Concepción"),
    ("ur_Cos", "Constitución"),
    ("ur_Coo", "Copiapó"),
    ("ur_Coq", "Coquimbo"),
    ("ur_Coy", "Coyhaique"),
    ("ur_Cur", "Curicó"),
    ("ur_Ill", "Illapel"),
    ("ur_Iqu", "Iquique"),
    ("ur_LaF", "La Florida"),
    ("ur_LaL", "La Ligua"),
    ("ur_LaS", "La Serena"),
    ("ur_LaU", "La Unión"),
    ("ur_Lan", "Lanco"),
    ("ur_Leb", "Lebu"),
    ("ur_Lin", "Linares"),
    ("ur_Lod", "Los Andes"),
    ("ur_Log", "Los Ángeles"),
    ("ur_LosRios", "Los Ríos"),
    ("ur_Nunoa", "Ñuñoa"),
    ("ur_Maipu", "Maipú"),
    ("ur_Melipilla", "Melipilla"),
    ("ur_Oso", "Osorno"),
    ("ur_Ova", "Ovalle"),
    ("ur_Pan", "Panguipulli"),
    ("ur_Par", "Parral"),
    ("ur_Pic", "Pichilemu"),
    ("ur_Por", "Porvenir"),
    ("ur_PuM", "Puerto Montt"),
    ("ur_PuN", "Puerto Natales"),
    ("ur_PuV", "Puerto Varas"),
    ("ur_PuA", "Punta Arenas"),
    ("ur_Qui", "Quillota"),
    ("ur_Ran", "Rancagua"),
    ("ur_SaA", "San Antonio"),
    ("ur_SanBernardo", "San Bernardo"),
    ("ur_Sar", "San Carlos"),
    ("ur_SaF", "San Felipe"),
    ("ur_SaD", "San Fernando"),
    ("ur_SaV", "San Vicente de Tagua Tagua"),
    ("ur_SaZ", "Santa Cruz"),
    ("ur_SaC", "Santiago Centro"),
    ("ur_SaN", "Santiago Norte"),
    ("ur_SaO", "Santiago Oriente"),
    ("ur_SaP", "Santiago Poniente"),
    ("ur_SaS", "Santiago Sur"),
    ("ur_TaT", "Tal-Tal"),
    ("ur_Tac", "Talca"),
    ("ur_Tah", "Talcahuano"),
    ("ur_Tem", "Temuco"),
    ("ur_Toc", "Tocopilla"),
    ("ur_Vld", "Valdivia"),
    ("ur_Val", "Vallenar"),
    ("ur_Vlp", "Valparaíso"),
    ("ur_Vic", "Victoria"),
    ("ur_ViA", "Villa Alemana"),
    ("ur_ViR", "Villarrica"),
    ("ur_ViM", "Viña del Mar"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    dte_sii_environment = fields.Selection(
        [
            ("demo", "Demo / Simulated"),
            ("certification", "Certification"),
            ("production", "Production"),
        ],
        string="DTE SII Environment",
        default="demo",
        required=True,
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    dte_sii_sender_vat = fields.Char(
        string="SII Authorized Sender RUT",
        groups="l10n_cl_dte_core.group_dte_manager",
        help="RUT authorized by SII to transmit the company's electronic documents.",
    )
    dte_sii_resolution_number = fields.Char(
        string="SII Resolution Number",
        help=(
            "Resolution number authorizing the company to issue electronic tax "
            "documents."
        ),
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    dte_sii_resolution_date = fields.Date(
        string="SII Resolution Date",
        groups="l10n_cl_dte_core.group_dte_manager",
    )
    dte_sii_regional_office = fields.Selection(
        SII_REGIONAL_OFFICES,
        string="SII Regional Office",
        help=(
            "Select the SII regional office associated with the company's "
            "registered address."
        ),
        groups="l10n_cl_dte_core.group_dte_manager",
    )
