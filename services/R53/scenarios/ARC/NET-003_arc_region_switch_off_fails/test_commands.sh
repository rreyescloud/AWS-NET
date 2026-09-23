# Prueba 4: Apagar ambos (debe fallar por safety rule)
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-1>","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-2>","RoutingControlState":"Off"}]' \
  --region us-west-2 \
  --endpoint-url "https://d33ae326.route53-recovery-cluster.us-west-2.amazonaws.com/v1" \
  --profile lab

# Prueba 5: Mismo pero desde eu-west-1
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-1>","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-2>","RoutingControlState":"Off"}]' \
  --region eu-west-1 \
  --endpoint-url "https://527f532c.route53-recovery-cluster.eu-west-1.amazonaws.com/v1" \
  --profile lab

# Prueba 6: Override safety rule (fuerza el OFF de ambos)
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-1>","RoutingControlState":"Off"},{"RoutingControlArn":"arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/routingcontrol/<routing-control-2>","RoutingControlState":"Off"}]' \
  --safety-rules-to-override '["arn:aws:route53-recovery-control::<LAB_ACCOUNT_ID>:controlpanel/<control-panel-id>/safetyrule/<safety-rule-id>"]' \
  --region us-west-2 \
  --endpoint-url "https://d33ae326.route53-recovery-cluster.us-west-2.amazonaws.com/v1" \
  --profile lab
