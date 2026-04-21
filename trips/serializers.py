from rest_framework import serializers


class TripPlanSerializer(serializers.Serializer):
    current_location = serializers.CharField(max_length=500)
    pickup_location = serializers.CharField(max_length=500)
    dropoff_location = serializers.CharField(max_length=500)
    cycle_used_hrs = serializers.FloatField(min_value=0.0, max_value=70.0)
    trip_start = serializers.DateTimeField(required=False, allow_null=True)
    log_timezone = serializers.CharField(max_length=64, required=False, default="UTC")
