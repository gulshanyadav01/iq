from django.contrib import admin

from master_data.models import (
    CarrierMaster,
    FuelTypeMaster,
    InsurerMaster,
    MasterDataUpload,
    PlanTypeMaster,
    PolicyTypeMaster,
    VehicleClassMaster,
    VehicleMaster,
    VehicleSubClassMaster,
)


READONLY_TIMESTAMPS = ("id", "created_at", "updated_at")


@admin.register(InsurerMaster)
class InsurerMasterAdmin(admin.ModelAdmin):
    list_display = ("insurer_id", "product_id", "name")
    list_filter = ("product_id",)
    search_fields = ("name", "name_normalized", "insurer_id")
    readonly_fields = READONLY_TIMESTAMPS
    ordering = ("name", "product_id")


@admin.register(VehicleMaster)
class VehicleMasterAdmin(admin.ModelAdmin):
    list_display = ("vehicle_type", "vehicle_id", "product_id")
    list_filter = ("product_id",)
    search_fields = ("vehicle_type",)
    readonly_fields = READONLY_TIMESTAMPS
    ordering = ("vehicle_type",)


@admin.register(VehicleSubClassMaster)
class VehicleSubClassMasterAdmin(admin.ModelAdmin):
    list_display = (
        "pb_vehicle_subclass_id",
        "subclass_name",
        "canonical_name",
        "product",
        "product_id",
    )
    list_filter = ("product", "product_id")
    search_fields = ("subclass_name", "canonical_name")
    readonly_fields = READONLY_TIMESTAMPS
    ordering = ("product", "pb_vehicle_subclass_id")


@admin.register(VehicleClassMaster)
class VehicleClassMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "vehicle_class_id")
    search_fields = ("name",)
    readonly_fields = READONLY_TIMESTAMPS


@admin.register(PolicyTypeMaster)
class PolicyTypeMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "policy_id")
    search_fields = ("name",)
    readonly_fields = READONLY_TIMESTAMPS


@admin.register(FuelTypeMaster)
class FuelTypeMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "fuel_id")
    search_fields = ("name",)
    readonly_fields = READONLY_TIMESTAMPS


@admin.register(PlanTypeMaster)
class PlanTypeMasterAdmin(admin.ModelAdmin):
    list_display = ("plan_type", "is_zero_dep", "plan_type_id")
    list_filter = ("is_zero_dep",)
    search_fields = ("plan_type",)
    readonly_fields = READONLY_TIMESTAMPS


@admin.register(CarrierMaster)
class CarrierMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "carrier_id")
    search_fields = ("name",)
    readonly_fields = READONLY_TIMESTAMPS


@admin.register(MasterDataUpload)
class MasterDataUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "uploaded_by", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("uploaded_by__email", "id")
    readonly_fields = READONLY_TIMESTAMPS + ("file", "uploaded_by", "summary", "error_message")
    ordering = ("-created_at",)
