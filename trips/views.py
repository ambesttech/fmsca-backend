import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from trips.serializers import TripPlanSerializer
from trips.services.plan_builder import build_trip_plan

logger = logging.getLogger(__name__)


class TripPlanView(APIView):
    def post(self, request):
        ser = TripPlanSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            data = build_trip_plan(ser.validated_data)
            return Response(data, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Trip plan failed")
            return Response(
                {"detail": "Upstream routing or geocoding failed. Try again in a moment."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
